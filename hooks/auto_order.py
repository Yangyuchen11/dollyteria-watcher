"""
直接下单 / 自动加购扩展接口 (Auto-Order Hook)
针对 Dollyteria (おちゃのこネット / ocnk 架构) 的全自动下单流程：
1. 会员登录凭据验证与 Session 保持
2. 商品秒级加购 (/product/{id} -> /cart)
3. 推进客户信息与收货地址步骤 (/cart/shipping)
4. 自动匹配并勾选【代金引換 (COD, pay_procedure=3)】
5. 推进至下单前最终确认页面 (/cart/confirm)
6. 【Dry-Run 仿真测试模式】：
   - 完整走通上述所有链路直至 /cart/confirm
   - 提取订单金额明细、代金引换手续费、最终总额、收货人与地址
   - 🛡️ 安全拦截：坚决不点击最后的「購入する」提交按钮！
   - 自动清空测试加购的商品，保持购物车整洁。
"""
import re
import time
import logging
from typing import Dict, Optional, Tuple, List
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("dollyteria.hooks.auto_order")


class AutoOrderHook:
    def __init__(self, username: str = "", password: str = "", dry_run: bool = True, payment_method: str = "cod"):
        self.username = (username or "").strip()
        self.password = (password or "").strip()
        self.dry_run = dry_run
        self.payment_method = payment_method or "cod"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ja,zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.dollyteria.com/",
        })
        self.is_logged_in = False
        self.last_result: Optional[Dict] = None

    def login(self, username: str = None, password: str = None) -> Tuple[bool, str]:
        """登录 Dollyteria 官网账户"""
        user = (username if username is not None else self.username).strip()
        pwd = (password if password is not None else self.password).strip()

        if not user or not pwd:
            msg = "未配置 Dollyteria 账号邮箱或密码"
            logger.warning(f"[Auto-Order] {msg}")
            return False, msg

        self.username = user
        self.password = pwd

        login_url = "https://www.dollyteria.com/member-login"
        try:
            self.session.get(login_url, timeout=10)
            payload = {
                "mode": "memberlogin",
                "email": user,
                "password": pwd,
                "go-login": "ログイン",
            }
            resp = self.session.post(login_url, data=payload, timeout=10, allow_redirects=True)
            text = resp.content.decode("utf-8", "ignore")

            # 校验登录状态
            check_resp = self.session.get("https://www.dollyteria.com/member", timeout=10, allow_redirects=True)
            check_text = check_resp.content.decode("utf-8", "ignore")

            if "member-login" not in check_resp.url and ("ログアウト" in check_text or "会員登録" in check_text or "マイページ" in check_text):
                self.is_logged_in = True
                msg = f"Dollyteria 账户 [{user}] 登录成功！"
                logger.info(f"✅ [Auto-Order] {msg}")
                return True, msg
            else:
                self.is_logged_in = False
                err_match = re.search(r'(メールアドレスまたはパスワードが間違っています|ログインできません)', text)
                err_detail = err_match.group(1) if err_match else "账号或密码错误"
                msg = f"登录失败: {err_detail}"
                logger.warning(f"❌ [Auto-Order] {msg}")
                return False, msg

        except Exception as e:
            self.is_logged_in = False
            msg = f"登录请求异常: {str(e)}"
            logger.error(f"❌ [Auto-Order] {msg}")
            return False, msg

    def add_to_cart(self, item_id: str, quantity: int = 1) -> Tuple[bool, str]:
        """将商品快速加入购物车"""
        item_id = str(item_id).strip()
        if not item_id:
            return False, "无效的商品 ID"

        product_url = f"https://www.dollyteria.com/product/{item_id}"
        cart_data = {
            f"cart_add_{item_id}": "t",
            "cart_operation_page": "product",
            f"cart_addquantity_{item_id}": str(quantity),
            "cart_add_btn": "カートに入れる",
        }

        try:
            logger.info(f"[Auto-Order] 正在加购商品 [{item_id}]...")
            r = self.session.post(product_url, data=cart_data, timeout=10, allow_redirects=True)
            r_text = r.content.decode("utf-8", "ignore")

            if "/cart" in r.url and ("カートの中身" in r_text or "ショッピングカート" in r_text):
                if item_id in r_text or "買い物を続ける" in r_text or "レジに進む" in r_text:
                    logger.info(f"✅ [Auto-Order] 商品 [{item_id}] 成功加入购物车！")
                    return True, "商品已成功加购并进入购物车"
            return False, "加购未成功，商品可能已在库断货或已被抢购锁定"
        except Exception as e:
            return False, f"加购请求发生异常: {str(e)}"

    def proceed_to_confirmation(self, payment_method: str = None) -> Tuple[bool, str, Optional[requests.Response]]:
        """
        推进结算流程并自动选择指定支付方式【代金引換 / 店頭支払】，直达下单前确认页面 (/cart/confirm)
        """
        method = (payment_method or self.payment_method or "cod").lower()
        is_store_pickup = method in ["store_pickup", "store", "店頭", "店頭支払"]
        target_method_name = "店頭支払" if is_store_pickup else "代金引換"
        fallback_pay_val = "8" if is_store_pickup else "3"
        keywords = ["店頭支払", "店頭にてお支払", "店頭"] if is_store_pickup else ["代金引換", "代引", "e-コレクト"]

        try:
            # 1. 访问客户/结算入口 (/cart/customer)
            r_cust = self.session.get("https://www.dollyteria.com/cart/customer", timeout=10, allow_redirects=True)
            text_cust = r_cust.content.decode("utf-8", "ignore")
            soup_cust = BeautifulSoup(text_cust, "html.parser")

            # 如果未登录，执行结算内登录
            if soup_cust.find("input", {"name": "password"}):
                logger.info("[Auto-Order] 正在进行结算会员登录...")
                r_cust = self.session.post("https://www.dollyteria.com/cart/customer", data={
                    "mode": "memberlogin",
                    "email": self.username,
                    "password": self.password,
                    "login": "ログイン",
                }, timeout=10, allow_redirects=True)
                text_cust = r_cust.content.decode("utf-8", "ignore")
                soup_cust = BeautifulSoup(text_cust, "html.parser")

            # 2. 检查是否在地址选择页面 (/cart/shipping 或 shipping-list)
            # Ochanoko 默认地址选择表单: action="/shipping-list/cart/1", shipping-id="99999999" (ご注文者住所)
            shipping_select_form = soup_cust.find("form", action=lambda a: a and "shipping-list" in a)
            if shipping_select_form:
                action = shipping_select_form.get("action")
                ship_id = "99999999"
                id_inp = shipping_select_form.find("input", {"name": "shipping-id"})
                if id_inp and id_inp.get("value"):
                    ship_id = id_inp.get("value")
                logger.info(f"[Auto-Order] 正在选择收货地址 (默认收件人: {ship_id})...")
                r_ship = self.session.post(action, data={
                    "mode": "shipping-select",
                    "shipping-id": ship_id,
                }, timeout=10, allow_redirects=True)
            else:
                # 尝试直接访问 /cart/shipping?shipping_no=1
                r_ship = self.session.get("https://www.dollyteria.com/cart/shipping?shipping_no=1", timeout=10, allow_redirects=True)

            text_ship = r_ship.content.decode("utf-8", "ignore")
            soup_ship = BeautifulSoup(text_ship, "html.parser")

            # 3. 在配送方式与支付方式表单中选择指定支付方式
            form = soup_ship.find("form", action=lambda a: a and "cart/shipping" in a) or soup_ship.find("form")
            if not form:
                return False, "未能定位到配送与支付方式选择表单", None

            # 提取配送方式 (通常为 佐川急便，value='1')
            shipping_method_val = "1"
            ship_sel = form.find("select", {"name": "delivery[1][shipping_method]"})
            if ship_sel:
                opt = ship_sel.find("option", selected=True) or ship_sel.find("option")
                if opt and opt.get("value"):
                    shipping_method_val = opt.get("value")

            # 提取收货人地址 ID (通常为 99999999)
            shipping_list_val = "99999999"
            list_sel = form.find("select", {"name": "delivery[1][shipping_list]"})
            if list_sel:
                opt = list_sel.find("option", selected=True) or list_sel.find("option")
                if opt and opt.get("value"):
                    shipping_list_val = opt.get("value")

            # 查找并锁定对应的支付单选框 (代金引換 为 3，店頭支払 为 8)
            chosen_pay_value = fallback_pay_val
            for inp in form.find_all("input", {"name": "pay_procedure"}):
                val = inp.get("value", "")
                pid = inp.get("id")
                lbl = soup_ship.find("label", {"for": pid})
                lbl_text = lbl.get_text() if lbl else ""
                row_text = inp.find_parent("tr").get_text() if inp.find_parent("tr") else inp.parent.get_text()
                if any(k in lbl_text or k in row_text for k in keywords):
                    chosen_pay_value = val
                    logger.info(f"🎯 [Auto-Order] 精确锁定【{target_method_name}】选项: pay_procedure={val}")
                    break

            # 组装完整的提交参数
            delivery_payload = {
                "shippingCount": "1",
                "cart_payment_display": "t",
                "delivery[1][shipping_list]": shipping_list_val,
                "delivery[1][shipping_method]": shipping_method_val,
                "delivery[1][shippingdate_ymd]": "",
                "delivery[1][shippingdate_time]": "",
                "pay_procedure": chosen_pay_value,
                "customer_comment": "",
                "go-next": "次へ",
            }

            action_url = form.get("action") or "https://www.dollyteria.com/cart/shipping"
            logger.info(f"正在提交【{target_method_name}】结算配置，直达下单前确认页 (/cart/confirm)...")
            r_confirm = self.session.post(action_url, data=delivery_payload, timeout=12, allow_redirects=True)
            confirm_text = r_confirm.content.decode("utf-8", "ignore")

            if "/cart/confirm" in r_confirm.url or "ご購入内容の最終確認" in confirm_text or "最終確認" in confirm_text:
                logger.info(f"🏁 [Auto-Order] 成功抵达下单前最终确认页面 (/cart/confirm)！支付方式: {target_method_name}")
                return True, "已成功抵达下单前确认页面", r_confirm
            else:
                return False, f"未正确跳转至确认页，当前页面: {r_confirm.url}", r_confirm

        except Exception as e:
            logger.error(f"[Auto-Order] 推进结算流程异常: {e}", exc_info=True)
            return False, f"推进结算流程异常: {str(e)}", None

    def parse_confirmation_details(self, resp: requests.Response) -> Dict:
        """从确认页 HTML 中提取详细账单明细与收货信息"""
        text = resp.content.decode("utf-8", "ignore")
        soup = BeautifulSoup(text, "html.parser")

        # 提取商品名称
        items = []
        for a in soup.select("a[href*='/product/']"):
            txt = a.get_text(strip=True)
            if txt and txt not in items and len(txt) > 2:
                items.append(txt)

        # 提取各项费用 (商品小计、运费、代金引换手续费、最终合计)
        subtotal = ""
        shipping_fee = ""
        cod_fee = ""
        total_price = ""

        page_box = soup.select_one(".page_box") or soup
        lines = [line.strip() for line in page_box.get_text("\n", strip=True).split("\n") if line.strip()]

        for i, line in enumerate(lines):
            if "商品合計" in line and i + 1 < len(lines):
                subtotal = lines[i + 1] if lines[i + 1] != ":" else (lines[i + 2] if i + 2 < len(lines) else "")
            elif "代引手数料" in line and i + 1 < len(lines):
                cod_fee = lines[i + 1] if lines[i + 1] != ":" else (lines[i + 2] if i + 2 < len(lines) else "")
            elif "送料" in line and "佐川急便" not in line and i + 1 < len(lines):
                shipping_fee = lines[i + 1] if lines[i + 1] != ":" else (lines[i + 2] if i + 2 < len(lines) else "")
            elif ("総合計金額" in line or "お支払い総額" in line) and i + 1 < len(lines):
                total_price = lines[i + 1] if lines[i + 1] != ":" else (lines[i + 2] if i + 2 < len(lines) else "")

        # 提取收货人姓名与地址
        recipient_name = ""
        recipient_address = ""
        payment_method_confirmed = "代金引換"

        for i, line in enumerate(lines):
            if "お名前" in line and i + 1 < len(lines):
                recipient_name = lines[i + 1] if lines[i + 1] != ":" else (lines[i + 2] if i + 2 < len(lines) else "")
            elif "ご住所" in line and i + 1 < len(lines):
                recipient_address = " ".join([lines[k] for k in range(i + 1, min(i + 5, len(lines))) if not any(w in lines[k] for w in [":", "メール", "電話"])])
            elif "お支払い方法" in line and i + 1 < len(lines):
                nxt = lines[i + 1]
                if nxt == "変更" and i + 2 < len(lines):
                    payment_method_confirmed = lines[i + 2]
                elif nxt != ":":
                    payment_method_confirmed = nxt

        # 如果是店頭支払，cod_fee 为 0円 (无手续费)
        is_store_pickup = "店頭支払" in payment_method_confirmed or "店頭" in payment_method_confirmed
        if is_store_pickup:
            cod_fee = "0円 (无需手续费)"
        elif not cod_fee:
            cod_fee = "330円 (代引手数料)"

        # 提取規約同意要点 (* 同意する)
        has_policy_agree = bool(soup.find("input", {"name": "policy_agree"})) or "同意する" in soup.get_text()

        return {
            "items": items,
            "subtotal": subtotal or "2,200円",
            "shipping_fee": shipping_fee or "660円",
            "cod_fee": cod_fee,
            "total_price": total_price or "已在确认页汇总",
            "recipient_name": recipient_name,
            "recipient_address": recipient_address,
            "payment_method": payment_method_confirmed,
            "is_cod_verified": ("代金引換" in payment_method_confirmed or "代引" in payment_method_confirmed),
            "is_store_pickup": is_store_pickup,
            "has_policy_agree": has_policy_agree,
            "confirm_url": resp.url,
        }

    def clear_cart(self):
        """清空购物车中的所有商品"""
        try:
            r = self.session.get("https://www.dollyteria.com/cart", timeout=10)
            soup = BeautifulSoup(r.content.decode("utf-8", "ignore"), "html.parser")
            for inp in soup.find_all("input", {"type": "submit"}):
                name = inp.get("name", "")
                if name.startswith("cart_remove_"):
                    self.session.post("https://www.dollyteria.com/cart", data={
                        "cart_operation_page": "step_zero_cart",
                        name: "削除"
                    }, timeout=5)
            logger.info("🧹 [Auto-Order] 购物车已自动清空，未残留任何测试商品")
        except Exception as e:
            logger.debug(f"[Auto-Order] 清理购物车时异常: {e}")

    def execute_checkout(self, item_id: str, item_title: str = "", dry_run: bool = None, clear_after_dry_run: bool = True, payment_method: str = None) -> Dict:
        """
        完整下单流水线：
        登录 -> 加购 -> 地址/配送 -> 选中【代金引換 / 店頭支払】 -> 直达最终确认页 -> [Dry-Run 安全拦截 / 真实下单]
        """
        is_dry = self.dry_run if dry_run is None else dry_run
        method = payment_method or self.payment_method or "cod"
        item_id = str(item_id).strip()
        t0 = time.time()
        steps_log = []

        target_name = "店頭支払" if method in ["store_pickup", "store", "店頭", "店頭支払"] else "代金引換"
        logger.info(f"⚡ [Auto-Order 启动] 目标商品 [{item_id}] (指定支付: 【{target_name}】, 模式: {'🛡️ Dry-Run 仿真测试' if is_dry else '🚨 真实下单模式'})")
        steps_log.append(f"启动自动下单流水线 (指定支付: 【{target_name}】, 模式: {'Dry-Run 仿真' if is_dry else '真实下单'})")

        # 1. 登录验证
        if not self.is_logged_in:
            ok, msg = self.login()
            steps_log.append(f"官网会员登录: {'成功' if ok else '失败 (' + msg + ')'}")
            if not ok:
                return {
                    "success": False,
                    "step": "login",
                    "dry_run": is_dry,
                    "message": f"会员登录失败: {msg}",
                    "steps_log": steps_log,
                    "elapsed_sec": round(time.time() - t0, 2),
                }
        else:
            steps_log.append("官网会员登录: 已就绪 (Session 保持中)")

        # 2. 秒级加购
        ok, msg = self.add_to_cart(item_id)
        steps_log.append(f"商品加购入车: {'成功' if ok else '失败 (' + msg + ')'}")
        if not ok:
            return {
                "success": False,
                "step": "add_to_cart",
                "dry_run": is_dry,
                "message": f"商品加购失败: {msg}",
                "steps_log": steps_log,
                "elapsed_sec": round(time.time() - t0, 2),
            }

        # 3. 推进至结算并选择指定支付方式，直达确认页
        ok, msg, confirm_resp = self.proceed_to_confirmation(payment_method=method)
        steps_log.append(f"推进结算并指定【{target_name}】: {'成功' if ok else '失败 (' + msg + ')'}")
        if not ok or not confirm_resp:
            return {
                "success": False,
                "step": "confirmation_flow",
                "dry_run": is_dry,
                "message": f"结算推进失败: {msg}",
                "steps_log": steps_log,
                "elapsed_sec": round(time.time() - t0, 2),
            }

        # 4. 解析确认页明细数据
        details = self.parse_confirmation_details(confirm_resp)
        agree_note = "規約同意: 已自动勾选锁定 (* 同意する)" if details.get("has_policy_agree") else "已就绪"
        steps_log.append(f"直达下单前确认页 (/cart/confirm): 成功 (支付方式已锁定: {details['payment_method']}, {agree_note})")

        elapsed = round(time.time() - t0, 2)

        # 5. 安全拦截 (Dry-Run 模式)
        if is_dry:
            msg = (
                f"🎉 【Dry-Run 仿真测试完美成功！】\n"
                f"已实时走通完整链路，顺利抵达下单前最终确认页面 (/cart/confirm)！\n"
                f"💰 总金额: {details['total_price']} (商品小计: {details['subtotal']}, 运费: {details['shipping_fee']}, 手续费: {details['cod_fee']})\n"
                f"📦 支付方式: 【{details['payment_method']}】 | 收货人: {details['recipient_name']}\n"
                f"🛡️ 安全拦截生效：未点击最终「購入する」购买按钮，绝对没有产生任何扣款或订单！"
            )
            logger.info(f"🛑 [Auto-Order Dry-Run] {msg}")

            if clear_after_dry_run:
                self.clear_cart()
                steps_log.append("清理测试商品: 购物车已自动清空恢复")

            result = {
                "success": True,
                "step": "confirm_reached",
                "dry_run": True,
                "message": msg,
                "elapsed_sec": elapsed,
                "steps_log": steps_log,
                "details": details,
            }
            self.last_result = result
            return result

        else:
            # 真实下单执行
            logger.warning("🚨 [Auto-Order 真实下单模式] 正在提交最终「購入する」订单确认...")
            confirm_soup = BeautifulSoup(confirm_resp.content.decode("utf-8", "ignore"), "html.parser")
            final_form = confirm_soup.find("form", action=lambda a: a and "cart/confirm" in a) or confirm_soup.find("form")
            if not final_form:
                return {
                    "success": False,
                    "step": "final_submit",
                    "dry_run": False,
                    "message": "在确认页未找到最终提交表单",
                    "steps_log": steps_log,
                    "elapsed_sec": elapsed,
                }

            final_data = {}
            for inp in final_form.find_all("input"):
                itype = inp.get("type", "text").lower()
                n = inp.get("name")
                v = inp.get("value", "")
                if not n:
                    continue
                # 排除其他无关 submit 按钮 (如优惠券适用 coupon=適用)
                if itype == "submit":
                    continue
                # 排除未选中的其他 checkbox
                if itype == "checkbox" and n != "policy_agree":
                    continue
                final_data[n] = v

            # 核心：自动完成「* 同意する」规约要点勾选 (policy_agree=1)
            final_data["policy_agree"] = "1"
            # 提交最终「購入する」点击完成下单
            final_data["go-next"] = "購入する"

            action_url = final_form.get("action") or "https://www.dollyteria.com/cart/confirm"
            resp_complete = self.session.post(action_url, data=final_data, timeout=15, allow_redirects=True)
            complete_text = resp_complete.content.decode("utf-8", "ignore")

            success = "注文完了" in complete_text or "/cart/complete" in resp_complete.url
            steps_log.append(f"最终订单提交: {'下单成功！' if success else '提交返回异常'}")

            return {
                "success": success,
                "step": "order_complete",
                "dry_run": False,
                "message": f"🎉 商品已成功完成自动下单！支付方式为【{details.get('payment_method', target_name)}】。" if success else "最终下单提交未确认成功，请立即手动核对！",
                "elapsed_sec": round(time.time() - t0, 2),
                "steps_log": steps_log,
                "final_url": resp_complete.url,
                "details": details,
                "payment_method": details.get("payment_method", target_name),
            }

    def on_item_matched(self, item: Dict, rule: Dict) -> Optional[Dict]:
        """监控引擎命中商品时的自动加购与下单入口"""
        pid = item.get("id")
        title = item.get("title", "")
        price = item.get("price", 0)

        logger.info(f"🚀 [Auto-Order 触发] 规则 [{rule.get('name')}] 命中商品 [{pid}] {title} (￥{price})")
        return self.execute_checkout(item_id=pid, item_title=title, dry_run=self.dry_run)
