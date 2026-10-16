# MoviePilot V3 Prowlarr 搜索插件

这是一个 MoviePilot V3 原生搜索插件。它把 Prowlarr 已启用索引器返回的 torrent 结果显示在 MoviePilot 搜索页中，不使用 MoviePilot 站点管理、V2 兼容层或 monkey-patch。

本插件不需要 MoviePilot 官方用户认证，也不需要 IYUU、CookieCloud 或虚拟站点。MoviePilot 自身 Web/API 的正常本地访问控制仍然有效；本插件不绕过宿主安全机制。

## 兼容性与范围

- MoviePilot `>=3.1.0`。
- Prowlarr 必须能从 MoviePilot 容器网络访问。
- 只返回 torrent；Usenet/NZB 会被过滤。
- 默认 Prowlarr 地址为 `http://prowlarr:9696`。
- 插件只负责搜索与安全获取 torrent，不配置下载器或媒体目录。

现有媒体流程不会修改：

```text
qBittorrent
  → /mnt/user/downloads/qbittorrent
  → MoviePilot 复制整理到 /mnt/user/media/video
  → 硬链接到 /mnt/user/media/links
  → Jellyfin
```

## 配置

| 字段 | 用途 | 默认值/范围 |
|---|---|---|
| `enabled` | 启用搜索插件 | `false` |
| `base_url` | MoviePilot 容器内可访问的 Prowlarr 地址 | `http://prowlarr:9696` |
| `api_key` | Prowlarr → Settings → General 中的 API Key | 空，密码输入框 |
| `timeout` | 搜索和中继请求超时秒数 | `30`，限制为 `5..120` |
| `page_size` | 排序后返回给 MoviePilot 的结果数 | `100`，限制为 `1..1000` |
| `candidate_limit` | 每次从 Prowlarr 获取并参与相关度排序的候选数 | `500`，限制为 `1..1000`，且不会低于 `page_size` |

保存后，在 MoviePilot 搜索页正常输入影片、剧集或音乐名称即可。插件会先扩大候选池，再按 IMDb/TMDB 标识符、精确标题、完整短语、全部词项、模糊相似度、做种数及 Prowlarr 原始顺序稳定排序，最后按 `page_size` 对同一排序池切页；排序不会按相似度过滤候选。插件会忽略 MoviePilot 的站点参数，因为 Prowlarr 是本插件唯一的数据源。

## 安装方式

### 第三方插件仓库

把完整源码仓库发布到你控制的 Git 仓库，保留根目录的 `package.v3.json` 和 `plugins.v3/prowlarrsearch/`，再把仓库地址加入 MoviePilot 的第三方插件仓库设置。MoviePilot 会按 V3 索引发现 `ProwlarrSearch`。

### GitHub Release

创建 `ProwlarrSearch_v1.1.0` Release，并上传构建出的 `prowlarrsearch_v1.1.0.zip`。该 ZIP 只有一个 `prowlarrsearch/` 顶层目录和五个运行时 Python 文件，供 MoviePilot 的 Release 安装链使用。

### 本地开发仓库

将本仓库挂载到 MoviePilot 容器内的绝对路径，并把该路径写入 `PLUGIN_LOCAL_REPO_PATHS`。多个本地仓库用英文逗号分隔。示例：

```text
PLUGIN_LOCAL_REPO_PATHS=/config/local-plugins/ProwlarrSearch-MoviePilot-V3
```

本地目录必须同时包含 `package.v3.json` 和 `plugins.v3/prowlarrsearch/`。修改后按 MoviePilot 当前开发设置重载插件。

`prowlarrsearch_v1.1.0.zip` 是 GitHub Release 资产结构；除非运行中的 UI 明确提供本地 ZIP 上传功能，否则本项目不宣称当前 MoviePilot UI 可以直接上传本地 ZIP。

## 安全设计

Prowlarr 搜索结果中的代理下载 URL 会包含明文 `apikey`。插件不会把它直接交给 MoviePilot，而是生成一个有效期 24 小时、绑定当前 Prowlarr 地址和 API Key 的 HMAC 签名 loopback URL。该 URL 不含 API Key 或明文上游 URL。

下载中继会在任何网络请求前验证签名；跨源重定向后永久移除 `X-Api-Key`；torrent 内容在读取过程中限制为 20 MiB。插件日志不记录 API Key、令牌、完整 URL、重定向位置或响应正文。

## 本地验证

在仓库根目录创建 Python 3.11+ 环境并安装测试额外依赖，然后运行：

```text
python -m pip install -e ".[test]"
python -m pytest -q
python scripts/build_release.py --output dist/prowlarrsearch_v1.1.0.zip
```

自动化测试证明配置、请求、映射、MoviePilot 插件合同、签名中继、流式上限、日志保密、清单和 ZIP 结构；它不能替代真实 Unraid 环境验收。

## 实机验收

1. 确认 MoviePilot 与 Prowlarr 位于可互通的 Docker 网络。
2. 在插件中填写 `http://prowlarr:9696` 和有效 API Key，启用插件。
3. 在 MoviePilot 搜索页搜索一个已知资源，确认来源显示 Prowlarr 索引器名称。
4. 选择一个 torrent 结果下载，确认任务进入原 qBittorrent。
5. 确认 qBittorrent 保存路径仍对应 `/mnt/user/downloads/qbittorrent`。
6. 下载完成后确认 MoviePilot 仍复制整理到 `/mnt/user/media/video`。
7. 确认原硬链接到 `/mnt/user/media/links` 和 Jellyfin 扫描流程未改变。
8. 检查搜索响应中的 `enclosure`，确认它是 `127.0.0.1` 签名中继地址且不含 Prowlarr API Key。
9. 检查失败日志，确认没有 API Key 或完整上游下载 URL；宿主日志可能包含不带密钥的短期中继 URL。

## 故障排查

- 搜索为空：先确认 Prowlarr 自身能搜索、插件已启用、API Key 有效，并从 MoviePilot 容器内解析 `prowlarr` 主机名。
- 只有 NZB 结果：本插件有意只接入 torrent。
- 旧搜索结果无法下载：签名在 24 小时后过期；修改 Prowlarr 地址或 API Key 后也需要重新搜索。
- 安装后未出现：确认 MoviePilot 版本至少为 3.1.0，目录名为小写 `prowlarrsearch`，并重新加载 V3 插件目录。
