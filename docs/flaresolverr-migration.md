# FlareSolverr 迁移参考

本文档整理本项目中 FlareSolverr 的接入方式，方便迁移到其他项目时复用同样的 Cloudflare clearance 刷新思路。

## 目标

FlareSolverr 在本项目中用于自动获取访问 `https://grok.com` 所需的 Cloudflare cookies 和匹配的 `User-Agent`。

项目本身不直接处理 Cloudflare challenge，而是把目标页面访问交给 FlareSolverr。FlareSolverr 使用浏览器环境完成页面加载后，返回可复用的 cookies 和 `User-Agent`，项目再把这些值注入到后续真实业务请求中。

## Docker Compose 接入

本项目通过 `docker-compose.yml` 同时启动主服务和 FlareSolverr：

```yaml
services:
  grok2api:
    environment:
      GROK_PROXY__CLEARANCE__MODE: flaresolverr
      GROK_PROXY__CLEARANCE__FLARESOLVERR_URL: http://flaresolverr:8191
      GROK_PROXY__CLEARANCE__REFRESH_INTERVAL: "600"
      GROK_PROXY__CLEARANCE__TIMEOUT_SEC: "60"

  flaresolverr:
    container_name: flaresolverr
    image: ghcr.io/flaresolverr/flaresolverr:latest
    ports:
      - "127.0.0.1:8191:8191"
    environment:
      TZ: Asia/Shanghai
      LOG_LEVEL: info
    restart: unless-stopped
```

说明：

- `http://flaresolverr:8191` 是 Docker Compose 内部服务名访问地址。
- `127.0.0.1:8191:8191` 主要用于宿主机调试，容器间通信不依赖它。
- `GROK_PROXY__CLEARANCE__MODE=flaresolverr` 会启用自动 clearance 刷新。
- `GROK_PROXY__CLEARANCE__REFRESH_INTERVAL` 控制定时刷新间隔，单位秒。
- `GROK_PROXY__CLEARANCE__TIMEOUT_SEC` 控制单次解 challenge 的超时时间，单位秒。

## 配置映射

本项目支持 `GROK_` 前缀环境变量覆盖运行时配置。

双下划线 `__` 表示配置层级，例如：

```text
GROK_PROXY__CLEARANCE__MODE=flaresolverr
```

会映射为：

```toml
[proxy.clearance]
mode = "flaresolverr"
```

对应默认配置位于 `config.defaults.toml`：

```toml
[proxy.clearance]
mode = "none"
cf_cookies = ""
user_agent = ""
browser = "chrome136"
flaresolverr_url = ""
timeout_sec = 60
refresh_interval = 3600
```

迁移到其他项目时，建议保留类似配置结构：

```toml
[clearance]
mode = "none" # none | manual | flaresolverr
flaresolverr_url = ""
timeout_sec = 60
refresh_interval = 600
cf_cookies = ""
user_agent = ""
```

## 核心流程

本项目的 FlareSolverr 接入链路如下：

1. 服务启动时加载配置。
2. 如果 `proxy.clearance.mode = "flaresolverr"`，启动 clearance 刷新调度器。
3. 调度器启动后先 warm-up 一次，预先获取 cookies。
4. 每次业务请求前，项目申请一个 `ProxyLease`。
5. 如果当前出口没有可用 clearance bundle，就调用 FlareSolverr 获取。
6. FlareSolverr 返回 cookies 和 `User-Agent`。
7. 项目按出口代理和目标 host 缓存成 `ClearanceBundle`。
8. 后续请求自动附带 Cloudflare cookies、业务登录 cookie 和匹配的 `User-Agent`。
9. 如果遇到 `403` 或 `401`，项目把对应 bundle 标记失效，下次请求重新刷新。
10. 后台定时刷新时采用“先构建新 bundle，再替换旧 bundle”的方式；如果刷新失败，旧 bundle 继续可用。

## FlareSolverr 请求格式

本项目调用 FlareSolverr 的核心请求：

```json
{
  "cmd": "request.get",
  "url": "https://grok.com",
  "maxTimeout": 60000
}
```

如果当前业务请求使用代理，项目会把同一个代理传给 FlareSolverr：

```json
{
  "cmd": "request.get",
  "url": "https://grok.com",
  "maxTimeout": 60000,
  "proxy": {
    "url": "socks5://proxy-host:1080"
  }
}
```

这一点很重要：Cloudflare clearance 通常和出口 IP、浏览器环境、`User-Agent` 等因素相关。迁移时应尽量保证“解 challenge 的出口”和“真实业务请求的出口”一致。

## 返回值处理

FlareSolverr 成功后会返回类似结构：

```json
{
  "status": "ok",
  "solution": {
    "cookies": [
      {
        "name": "cf_clearance",
        "value": "...",
        "domain": ".grok.com"
      }
    ],
    "userAgent": "Mozilla/5.0 ..."
  }
}
```

本项目处理逻辑：

- 检查 `status == "ok"`。
- 读取 `solution.cookies`。
- 读取 `solution.userAgent`。
- 根据目标 host 过滤 cookies。
- 把 cookies 拼成 HTTP Cookie header 格式：

```text
cf_clearance=xxx; other_cookie=yyy
```

然后保存为：

```python
ClearanceBundle(
    cf_cookies="cf_clearance=xxx; other_cookie=yyy",
    user_agent="Mozilla/5.0 ...",
    affinity_key="direct 或 proxy_url",
    clearance_host="grok.com",
)
```

## 缓存模型

本项目按 `(affinity_key, clearance_host)` 缓存 clearance。

示例：

```text
("direct", "grok.com")
("socks5://proxy-a:1080", "grok.com")
("socks5://proxy-b:1080", "grok.com")
```

这样做的原因是不同出口代理拿到的 Cloudflare cookies 不能随便混用。迁移到代理池项目时，建议每个代理独立维护一份 clearance。

## 请求头注入

真实请求 Grok 时，本项目会构造 Cookie：

```text
sso=业务登录令牌; sso-rw=业务登录令牌; cf_clearance=Cloudflare令牌
```

同时使用 FlareSolverr 返回的 `User-Agent`：

```http
User-Agent: Mozilla/5.0 ...
Cookie: sso=...; sso-rw=...; cf_clearance=...
```

迁移时要注意：

- `cf_clearance` 只是 Cloudflare 通行 cookie，不等于业务登录态。
- 业务登录 cookie 和 Cloudflare cookie 需要合并到同一个 `Cookie` 请求头。
- `User-Agent` 应尽量使用 FlareSolverr 返回的值。
- 如果你的 HTTP 客户端支持浏览器指纹模拟，应让指纹、UA、client hints 尽量一致。

## 失效与刷新策略

本项目的失效判断：

- `403`：按 Cloudflare challenge 处理，标记 clearance 失效。
- `401`：按未授权处理，也会标记 clearance 失效。
- `429`：只作为限流反馈，不直接刷新 clearance。
- `5xx`：作为上游错误，不直接刷新 clearance。

刷新策略：

- 启动时 warm-up，避免首个真实请求等待解 challenge。
- 定时刷新，默认可设为 600 或 3600 秒。
- 刷新失败时保留旧 bundle，避免短暂故障导致所有请求失去 clearance。
- 并发请求同时发现 bundle 失效时，只允许一个协程调用 FlareSolverr，其他协程等待结果，避免重复解 challenge。

## 迁移 Checklist

迁移到其他项目时，建议按以下步骤实现：

1. 在部署层增加 FlareSolverr 服务。
2. 增加配置项：`clearance.mode`、`flaresolverr_url`、`timeout_sec`、`refresh_interval`。
3. 实现一个 FlareSolverr client，负责 `POST /v1`。
4. 从返回结果中提取 cookies 和 `UserAgent`。
5. 按出口代理和目标 host 缓存 clearance。
6. 请求业务接口前，从缓存中获取对应 clearance。
7. 将业务 cookie 和 Cloudflare cookies 合并到 `Cookie` 请求头。
8. 使用 FlareSolverr 返回的 `User-Agent`。
9. 对 `403`、`401` 做失效反馈，触发下一次刷新。
10. 增加后台定时刷新，刷新失败时保留旧值。

## 最小伪代码

```python
async def solve_clearance(flaresolverr_url, target_url, proxy_url=None, timeout_sec=60):
    payload = {
        "cmd": "request.get",
        "url": target_url,
        "maxTimeout": timeout_sec * 1000,
    }
    if proxy_url:
        payload["proxy"] = {"url": proxy_url}

    result = await post_json(f"{flaresolverr_url.rstrip('/')}/v1", payload)
    if result.get("status") != "ok":
        return None

    solution = result.get("solution", {})
    cookies = solution.get("cookies", [])
    user_agent = solution.get("userAgent", "")

    cookie_header = "; ".join(
        f"{cookie.get('name')}={cookie.get('value')}"
        for cookie in cookies
    )

    return {
        "cookies": cookie_header,
        "user_agent": user_agent,
    }
```

请求时：

```python
clearance = await clearance_store.get(proxy_url, "grok.com")

headers = {
    "User-Agent": clearance.user_agent,
    "Cookie": f"sso={sso_token}; sso-rw={sso_token}; {clearance.cookies}",
}
```

## 本项目相关代码位置

- `docker-compose.yml`：FlareSolverr 服务和环境变量配置。
- `config.defaults.toml`：`proxy.clearance` 默认配置。
- `app/platform/config/loader.py`：`GROK_` 环境变量到配置路径的映射。
- `app/main.py`：启动 `ProxyClearanceScheduler`。
- `app/control/proxy/scheduler.py`：clearance warm-up 和定时刷新。
- `app/control/proxy/__init__.py`：代理选择、bundle 缓存、失效处理。
- `app/control/proxy/providers/flaresolverr.py`：调用 FlareSolverr 获取 cookies。
- `app/dataplane/proxy/adapters/headers.py`：把 clearance cookies 和业务 cookies 注入请求头。
- `app/dataplane/proxy/adapters/profile.py`：解析 `User-Agent`、`cf_clearance` 和浏览器 profile。

## 常见注意点

- 如果使用代理池，不要把 A 代理解出的 clearance 用到 B 代理请求上。
- 如果使用直连，FlareSolverr 容器和主服务容器应处在相同网络出口下。
- `cf_clearance` 可能过期，必须有失败反馈和刷新机制。
- `User-Agent` 要和 clearance 来源保持一致。
- 不建议每个请求都调用 FlareSolverr，应缓存并复用。
- 后台刷新应采用“构建成功后再替换”的方式，避免刷新失败导致旧值被清空。
