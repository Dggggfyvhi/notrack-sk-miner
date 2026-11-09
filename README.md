# notrack-sk-miner

一个**纯 Python、零浏览器、零第三方依赖**的脚本，自动在 `nottrackai.com` 上注册账号、用临时邮箱收验证码、拿到 `sk`（API key），并自动校验 key 真伪与额度。

> 用途：测试「临时邮箱能不能收某站点的验证码」、批量拿测试用 sk、验证自己搭的站点邮件链路是否通畅。

## 环境要求

- 只要装了 **Python 3.6+**（电脑、Termux、Pydroid 手机终端都行）
- **不需要**：无头浏览器 / Node / playwright / pip 装任何第三方库

## 用法

```bash
python3 sk_miner.py [轮数] [每轮间隔秒数]
```

- 例 1：跑 3 轮，每轮之间等 5 分钟（推荐，避开站点限流）
  ```bash
  python3 sk_miner.py 3 300
  ```
- 例 2：只跑 1 轮
  ```bash
  python3 sk_miner.py
  ```

结果追加写到脚本同目录的 `sk_miner_results.txt`：

```
2026-11-10 00:10:16 | round=1 | email=mytemp...@fldubai.com | nick=... | recovery=... | sk=sk-notrack-... | models=200 chat=402
```

## 它会自检 key，直接告诉你三种情况

| 状态 | 含义 |
|---|---|
| `models=200` | key 是真的（站点认证通过） |
| `models=401` | key 无效 / 假的 |
| `chat=200` | key 有额度，**真正能用** |
| `chat=402` | key 是真的，但账号余额为 0 |

一句话：**`chat=200` 才能实际调用模型**；`chat=402` 只是没充值。

调用模型示例（拿到 `chat=200` 的 key 后）：

```python
import openai
client = openai.OpenAI(
    base_url="https://api.notrack.ai/v1",
    api_key="sk-notrack-xxxx",
)
r = client.chat.completions.create(
    model="notrack-uncensored",
    messages=[{"role": "user", "content": "你好"}],
)
print(r.choices[0].message.content)
```

## 重要提示（站长视角）

1. **站点限流**：`nottrackai.com` 对**同一 IP 短时间内发验证码有上限**（实测约 3 封，超了返回 `too_many_sends`）。所以：
   - 一次别跑太多轮（3 轮一批）；
   - 每轮之间留足够间隔（300 秒以上）；
   - 一批跑完后等 1 小时再跑下一批。
2. **免费额度**：新账号 `credits=0`，新拿的 key 是 `chat=402`（能用 key 认证但没额度调用）。这是站点计费侧的行为，不是脚本问题。
3. 端点走 `https://api.notrack.ai/v1`（不要用 `api.nottrackai.com`，它计费不同步）。

## 原理

站点的注册/发码/建 key 全部是 `POST https://nottrackai.com/api/account.php` 的 JSON 接口，只需带一个 `X-Requested-With: nt` 头 + 会话 cookie，不需要浏览器：

- `do=register`：注册，返回 `recovery_code` 并下发 `nt_session` cookie
- `do=apikey.email_start`：向指定邮箱发 6 位验证码
- `do=apikey.email_confirm`：用验证码确认邮箱
- `do=apikey.create`：创建 key，响应里的 `secret` 字段即 `sk`