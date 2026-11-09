#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nottrackai.com 自动注册拿 sk 脚本（纯 Python 标准库，无浏览器、无第三方依赖）
====================================================================
任何装了 Python 3.6+ 的终端（手机 Termux / Python 手机终端 / 电脑）都能直接跑。

用法：
    python3 sk_miner.py [轮数] [每轮间隔秒数]
例：
    python3 sk_miner.py 3 300     # 跑 3 轮，每轮之间等 300 秒
    python3 sk_miner.py           # 默认跑 1 轮

接口（已逆向验证，全部 POST https://nottrackai.com/api/account.php）：
    头必须带 X-Requested-With: nt（站点 CSRF 校验），会话 cookie 手动传递。
    do=register              注册账号（返回 recovery_code + Set-Cookie: nt_session）
    do=apikey.email_start    向指定邮箱发验证码（受每 IP 限流 too_many_sends）
    do=apikey.email_confirm  用 6 位验证码确认邮箱
    do=apikey.create         创建 key，响应字段 secret 就是 sk

自检（关键，字面报 401/402/200）：
    GET  https://api.notrack.ai/v1/models        -> 200=key真 / 401=key假(无效)
    POST https://api.notrack.ai/v1/chat/completions -> 200=有额度能用 / 402=key真但没额度

结果存到脚本同目录 sk_miner_results.txt。
"""
import json
import time
import random
import re
import sys
import os
import ssl
import urllib.request
import urllib.error
import http.client

SITE = "https://nottrackai.com"
API = SITE + "/api/account.php"
MODEL_API = "https://api.notrack.ai/v1"
RESULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sk_miner_results.txt")


def _make_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _extract_cookie(set_cookie_hdr):
    """从 Set-Cookie 头里取 nt_session=xxx。"""
    if not set_cookie_hdr:
        return None
    m = re.search(r'nt_session=([^;]+)', set_cookie_hdr)
    return m.group(1) if m else None


def post_account(payload, cookie=None, retries=4):
    """POST account.php，返回 (json_dict, nt_session_cookie值)。带 SSL 重试。"""
    data = json.dumps(payload).encode("utf-8")
    last_err = None
    for i in range(retries):
        try:
            headers = {
                "Content-Type": "application/json",
                "X-Requested-With": "nt",
                "Origin": SITE,
                "User-Agent": "Mozilla/5.0",
            }
            if cookie:
                headers["Cookie"] = "nt_session=" + cookie
            req = urllib.request.Request(API, data=data, headers=headers, method="POST")
            resp = urllib.request.urlopen(req, timeout=30, context=_make_ctx())
            body = resp.read().decode("utf-8", "replace")
            new_cookie = _extract_cookie(resp.headers.get("Set-Cookie"))
            try:
                return json.loads(body), (new_cookie or cookie)
            except Exception:
                return {"ok": False, "error": "bad_json", "raw": body[:200]}, (new_cookie or cookie)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                return json.loads(body), cookie
            except Exception:
                return {"ok": False, "error": "http_%d" % e.code}, cookie
        except Exception as e:
            last_err = e
            time.sleep(2 * (i + 1))  # 退避重试（SSL EOF 偶发）
    return {"ok": False, "error": "network: " + str(last_err)}, cookie


def get_http(url, headers=None, retries=4):
    """GET，返回 (status_code, body)。带 SSL 重试。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {}, method="GET")
            resp = urllib.request.urlopen(req, timeout=30, context=_make_ctx())
            return resp.getcode(), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    return 0, str(last)


# ---------- 临时邮箱（mail.tm） ----------
def make_mailtm():
    _, body = get_http("https://api.mail.tm/domains")
    try:
        domain = json.loads(body)["hydra:member"][0]["domain"]
    except Exception:
        domain = "fldubai.com"  # 兜底
    address = "mytemp%d@%s" % (int(time.time()), domain)
    pw = "Mm12345678"
    # 建号（POST）
    data = json.dumps({"address": address, "password": pw}).encode()
    req = urllib.request.Request("https://api.mail.tm/accounts", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30, context=_make_ctx()).read()
    except Exception:
        pass  # 账号可能已存在
    time.sleep(1.5)
    data2 = json.dumps({"address": address, "password": pw}).encode()
    req2 = urllib.request.Request("https://api.mail.tm/token", data=data2,
                                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        tok = json.loads(urllib.request.urlopen(req2, timeout=30, context=_make_ctx()).read().decode())
        return address, tok["token"]
    except Exception:
        return address, None


def mailtm_wait_code(token, max_wait=120):
    start = time.time()
    seen = set()
    while time.time() - start < max_wait:
        time.sleep(8)
        try:
            _, body = get_http("https://api.mail.tm/messages",
                               headers={"Authorization": "Bearer " + (token or "")})
            msgs = json.loads(body).get("hydra:member", [])
            for m in reversed(msgs):
                subj = m.get("subject", "") or ""
                c = re.search(r"\b(\d{6})\b", subj)
                if c and subj not in seen:
                    seen.add(subj)
                    return c.group(1)
            print("     等信中... %ds（已有 %d 封）" % (round(time.time() - start), len(msgs)))
        except Exception:
            pass
    return None


# ---------- nottrackai 流程 ----------
def register(cookie, nick, pw):
    r, c = post_account({"do": "register", "nickname": nick, "password": pw, "device": "web"}, cookie=cookie)
    return r, c


def email_start(cookie, email):
    r, c = post_account({"do": "apikey.email_start", "email": email}, cookie=cookie)
    return r, c


def email_confirm(cookie, code):
    r, c = post_account({"do": "apikey.email_confirm", "code": code}, cookie=cookie)
    return r, c


def create_key(cookie, label):
    r, c = post_account({"do": "apikey.create", "label": label,
                         "daily_cap_usd": 0, "expires_days": 0, "persona": "bare"}, cookie=cookie)
    return r, c


# ---------- 自检 ----------
def verify_sk(sk):
    """返回 (models状态, chat状态)。"""
    m, _ = get_http(MODEL_API + "/models", headers={"Authorization": "Bearer " + sk})
    conn = http.client.HTTPSConnection("api.notrack.ai", timeout=30,
                                       context=ssl._create_unverified_context())
    payload = json.dumps({"model": "notrack-uncensored",
                          "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4})
    try:
        conn.request("POST", "/v1/chat/completions", body=payload,
                     headers={"Authorization": "Bearer " + sk, "Content-Type": "application/json"})
        resp = conn.getresponse()
        chat_code = resp.status
        resp.read()
    except Exception:
        chat_code = 0
    conn.close()
    return m, chat_code


def status_name(code):
    if code == 200:
        return "200-可用✅"
    if code == 401:
        return "401-key无效❌"
    if code == 402:
        return "402-key真但没额度⚠️"
    return str(code)


# ---------- 单轮 ----------
def one_round(round_no, label):
    print("\n=== 第 %d 轮 ===" % round_no)
    try:
        address, token = make_mailtm()
        print("  [1/5] 临时邮箱: %s" % address)

        nick = "me_" + "".join(random.choices("23456789abcdefghjkmnpqrstuvwxyz", k=8))
        pw = "Nt@" + "".join(random.choices("23456789abcdefghjkmnpqrstuvwxyz", k=8)) + "x"
        reg, cookie = register(None, nick, pw)
        if not reg.get("ok"):
            return {"ok": False, "err": "注册失败: " + str(reg.get("error"))}
        recovery = reg.get("recovery_code")
        print("  [2/5] 注册成功, 恢复码: %s" % recovery)

        es, cookie = email_start(cookie, address)
        if not es.get("ok"):
            err = es.get("error", "") or es.get("code", "")
            hint = "（站每IP限流/限发，等冷却再试）" if ("too_many" in str(err) or "many_sends" in str(err)) else ""
            return {"ok": False, "err": "发码失败: " + str(err) + hint,
                    "recovery": recovery, "nick": nick, "pw": pw, "email": address}

        code = mailtm_wait_code(token, 120)
        if not code:
            return {"ok": False, "err": "没收到验证码（可能站限流）",
                    "recovery": recovery, "nick": nick, "pw": pw, "email": address}
        print("  [3/5] 收到验证码: %s" % code)

        ec, cookie = email_confirm(cookie, code)
        if not ec.get("ok"):
            return {"ok": False, "err": "确认失败: " + str(ec.get("error")),
                    "recovery": recovery, "nick": nick, "pw": pw, "email": address}
        print("  [4/5] 邮箱确认成功")

        ck, cookie = create_key(cookie, label)
        if not ck.get("ok"):
            return {"ok": False, "err": "创建失败: " + str(ck.get("error")),
                    "recovery": recovery, "nick": nick, "pw": pw, "email": address}
        sk = ck.get("secret")
        if not sk:
            return {"ok": False, "err": "create 成功但没 secret: " + json.dumps(ck)[:200],
                    "recovery": recovery, "nick": nick, "pw": pw, "email": address}
        print("  [5/5] SK: %s" % sk)

        m, c = verify_sk(sk)
        print("  自检: models=%s  chat=%s" % (status_name(m), status_name(c)))

        with open(RESULT_FILE, "a") as f:
            f.write("%s | round=%d | email=%s | nick=%s | recovery=%s | sk=%s | models=%s chat=%s\n" %
                    (time.strftime("%Y-%m-%d %H:%M:%S"), round_no, address, nick, recovery, sk, m, c))
        return {"ok": True, "sk": sk, "models": m, "chat": c, "recovery": recovery,
                "nick": nick, "pw": pw, "email": address}
    except Exception as e:
        return {"ok": False, "err": "异常: " + str(e)}


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    gap = int(sys.argv[2]) if len(sys.argv) > 2 else 180
    print("跑 %d 轮，每轮间隔 %d 秒（站每 IP 限发几封，别设太密）" % (rounds, gap))
    print("结果存到: %s" % RESULT_FILE)
    ok = 0
    for r in range(1, rounds + 1):
        res = one_round(r, "mine-%d" % r)
        if res.get("ok"):
            ok += 1
        else:
            print("  ❌ 失败: %s" % res.get("err"))
        if r < rounds:
            print("  [冷却 %d 秒]" % gap)
            time.sleep(gap)
    print("\n===== 完成：%d/%d 成功 =====" % (ok, rounds))


if __name__ == "__main__":
    main()