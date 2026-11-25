"""内网 bridge 客户端。

店铺管理模块的所有 Temu 上游请求都走 bridge（`bridge/server.js`）：
后端只做鉴权、校验与审计，协议细节留在 Node 侧。

设计约定：
- 只用标准库（与 `services/updater.py` 一致），不引入 HTTP 依赖；
- 每次请求带 `X-Bridge-Token`；未配置时抛 `BridgeError(NOT_CONFIGURED)`；
- bridge 的错误体统一是 `{"ok": false, "error": {stage, code, msg, retryAfterSeconds}}`，
  这里翻译成 `BridgeError`，由路由层映射 HTTP 状态。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from app.config import (
    STORE_BRIDGE_TIMEOUT,
    STORE_BRIDGE_TOKEN,
    STORE_BRIDGE_URL,
    STORE_LOGIN_TIMEOUT,
)


class BridgeError(Exception):
    """bridge 返回的业务错误或连接错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str = "bridge",
        status: int = 0,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.status = status
        self.retry_after = retry_after

    def as_dict(self) -> dict:
        payload = {"stage": self.stage, "code": self.code, "msg": self.message}
        if self.retry_after:
            payload["retry_after_seconds"] = self.retry_after
        return payload


def configured() -> bool:
    return bool(STORE_BRIDGE_URL)


def _decode_error(status: int, text: str) -> BridgeError:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    error = (payload or {}).get("error") or {}
    return BridgeError(
        str(error.get("code") or f"HTTP_{status}"),
        str(error.get("msg") or "店铺服务返回异常"),
        stage=str(error.get("stage") or "bridge"),
        status=status,
        retry_after=error.get("retryAfterSeconds"),
    )


def request(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    timeout: Optional[int] = None,
) -> Any:
    """调用 bridge 并返回 `data` 字段；失败统一抛 BridgeError。"""
    if not configured():
        raise BridgeError("NOT_CONFIGURED", "店铺管理服务未配置", stage="bridge")

    url = STORE_BRIDGE_URL + path
    if params:
        clean = {key: value for key, value in params.items() if value is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)

    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if STORE_BRIDGE_TOKEN:
        headers["X-Bridge-Token"] = STORE_BRIDGE_TOKEN

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout or STORE_BRIDGE_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise _decode_error(exc.code, text) from None
    except urllib.error.URLError as exc:
        raise BridgeError("BRIDGE_UNAVAILABLE", f"店铺服务不可用：{exc.reason}", stage="bridge") from None
    except TimeoutError:
        raise BridgeError("BRIDGE_TIMEOUT", "店铺服务响应超时", stage="bridge") from None

    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        raise BridgeError("BAD_RESPONSE", "店铺服务返回了非 JSON 内容", stage="bridge", status=status) from None

    if not payload.get("ok"):
        raise _decode_error(status, text)
    return payload.get("data")


def ping() -> bool:
    """轻量存活探测（不校验 token，bridge 的 /ping 是公开的）。"""
    if not configured():
        return False
    try:
        with urllib.request.urlopen(STORE_BRIDGE_URL + "/ping", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def login(
    account: str,
    password: str,
    mall_id: Optional[str] = None,
    verify_code: Optional[str] = None,
) -> dict:
    return request(
        "POST",
        "/session/login",
        body={
            "account": account,
            "password": password,
            "mallId": mall_id,
            # 触发风控时需要用户输入验证码，带了就走「密码 + 验证码」
            **({"verifyCode": verify_code} if verify_code else {}),
        },
        timeout=STORE_LOGIN_TIMEOUT,
    )


def send_login_code(account: str, contact_type: Optional[int] = None) -> dict:
    """发登录验证码（邮箱/手机号）。"""
    return request(
        "POST",
        "/session/send-code",
        body={
            "account": account,
            **({"contactType": contact_type} if contact_type is not None else {}),
        },
        timeout=STORE_LOGIN_TIMEOUT,
    )


def logout(account: str) -> dict:
    return request("POST", "/session/logout", body={"account": account})


def health(account: str) -> dict:
    return request("GET", "/health", params={"account": account})


def list_sessions() -> dict:
    return request("GET", "/health")


def goods_list(
    account: str,
    *,
    page: int = 1,
    page_size: int = 20,
    bind_site_ids: Optional[str] = None,
    site: Optional[str] = None,
    skc_site_status: Optional[int] = None,
    product_name: Optional[str] = None,
    product_skc_ids: Optional[list] = None,
) -> dict:
    return request(
        "GET",
        "/goods/list",
        params={
            "account": account,
            "page": page,
            "pageSize": page_size,
            "bindSiteIds": bind_site_ids,
            "site": site,
            # 0 = 未发布到任何站点，1 = 已发布到至少一个站点（唯一有效的状态筛选参数）
            "skcSiteStatus": skc_site_status,
            # 服务端支持的筛选（上游实测）
            "productName": product_name,
            "productSkcIds": _csv(product_skc_ids),
        },
    )


def goods_sites(account: str, *, force: bool = False) -> dict:
    return request("GET", "/goods/sites", params={"account": account, "force": "true" if force else None})


def _csv(values) -> Optional[str]:
    if not values:
        return None
    return ",".join(str(v) for v in values)


def goods_lifecycle(
    account: str,
    *,
    page_num: int = 1,
    page_size: int = 20,
    select_status: Optional[list] = None,
    keyword: Optional[str] = None,
    product_ids: Optional[list] = None,
    product_skc_ids: Optional[list] = None,
    product_sku_ids: Optional[list] = None,
    skc_ext_codes: Optional[list] = None,
    sku_ext_codes: Optional[list] = None,
    cat_ids: Optional[list] = None,
    site_ids: Optional[list] = None,
    time_type: Optional[int] = None,
    time_begin: Optional[int] = None,
    time_end: Optional[int] = None,
    remove_status: Optional[int] = None,
    price_review_status: Optional[list] = None,
    sample_qc_status: Optional[list] = None,
    todo_types: Optional[list] = None,
    punish_status: Optional[list] = None,
    limit_status: Optional[list] = None,
    re_sample_begin: Optional[int] = None,
    re_sample_end: Optional[int] = None,
) -> dict:
    """上新生命周期列表（robin/searchForSemiSupplier），分页参数是 pageNum。

    服务端支持全部筛选（上游实测），一次只取一页 —— 平台连续 8~10 次/分钟会 40001。
    """
    return request(
        "GET",
        "/goods/lifecycle",
        params={
            "account": account,
            "pageNum": page_num,
            "pageSize": page_size,
            "selectStatus": _csv(select_status),
            "keyword": keyword,
            "productIds": _csv(product_ids),
            "productSkcIds": _csv(product_skc_ids),
            "productSkuIds": _csv(product_sku_ids),
            "extCodes": _csv(skc_ext_codes),
            "skuExtCodes": _csv(sku_ext_codes),
            "catIds": _csv(cat_ids),
            "siteIds": _csv(site_ids),
            "timeType": time_type,
            "timeBegin": time_begin,
            "timeEnd": time_end,
            "removeStatus": remove_status,
            "priceReviewStatus": _csv(price_review_status),
            "sampleQcStatus": _csv(sample_qc_status),
            "todoTypes": _csv(todo_types),
            "punishStatus": _csv(punish_status),
            "limitStatus": _csv(limit_status),
            "reSampleBegin": re_sample_begin,
            "reSampleEnd": re_sample_end,
        },
        timeout=30,
    )


# ==================== 商品创建（temu-ops v1.1.0） ====================
def product_category_suggest(account: str, *, name_zh: str = "", name_en: str = "") -> dict:
    """按商品名推荐类目路径（Temu 官方流程：先写名称，系统给候选类目）。"""
    payload = {"account": account}
    if name_zh:
        payload["productNameZh"] = name_zh
    if name_en:
        payload["productNameEn"] = name_en
    return request("POST", "/product/category/suggest", body=payload, timeout=40)


def product_category_search(account: str, keyword: str, *, is_en: bool = False) -> dict:
    """类目关键字搜索（模糊，多条）。参数名是 searchText，对应官网分类搜索框。"""
    return request(
        "POST",
        "/product/category/search",
        body={"account": account, "keyword": keyword, "isEn": is_en},
        timeout=40,
    )


def product_category_hints(account: str) -> dict:
    """常用推荐类目（新建页顶部那 5 条）。"""
    return request("GET", "/product/category/hints", params={"account": account}, timeout=30)


def product_warehouses(account: str, site_id: Optional[int] = None) -> dict:
    """发货仓列表（含仓库名称 + 覆盖站点）。"""
    params = {"account": account}
    if site_id:
        params["siteId"] = site_id
    return request("GET", "/product/warehouses", params=params, timeout=40)


def product_constants(account: str) -> dict:
    """固定枚举：敏感类型、SKU 分类。"""
    return request("GET", "/product/constants", params={"account": account}, timeout=20)


def product_regions(account: str, *, refresh: bool = False) -> dict:
    """产地/制造地点可选值（国家 + 中国省份）。"""
    return request(
        "GET",
        "/product/regions",
        params={"account": account, "refresh": "1" if refresh else "0"},
        timeout=40,
    )


def product_size_templates(account: str, cat_id: int, site_id: int = 0, group_id: int = 0) -> dict:
    """尺码表模板列表（businessId 填 sizeTemplateIds）。

    ⚠️ 必须带 siteId 或 groupId：官网按「站点对应的尺码组」过滤，
    不带会拿到不分组/别的组的模板，提交 product/add 报 6000012。
    """
    params = {"account": account, "catId": cat_id}
    if site_id:
        params["siteId"] = site_id
    if group_id:
        params["groupId"] = group_id
    return request("GET", "/product/size-templates", params=params, timeout=40)


def product_size_spec_config(account: str, cat_id: int) -> dict:
    """尺码体系映射：站点 → 尺码组。"""
    return request(
        "GET",
        "/product/size-spec-config",
        params={"account": account, "catId": cat_id},
        timeout=40,
    )


def product_category_config(account: str, leaf_cat_id: int, site_id: int = 100) -> dict:
    """类目配置：哪些字段必填、标题长度上限、敏感属性要求。"""
    return request(
        "GET",
        "/product/category/config",
        params={"account": account, "leafCatId": leaf_cat_id, "siteId": site_id},
        timeout=40,
    )


def product_category_children(account: str, parent_cat_id: int = 0) -> dict:
    """逐级类目树：parent_cat_id=0 取一级。"""
    return request(
        "GET",
        "/product/category/children",
        params={"account": account, "parentCatId": parent_cat_id},
        timeout=40,
    )


def product_category_path(account: str, cat_ids: list) -> dict:
    """catId 列表反查完整类目路径（手填 ID 时显示中文名）。"""
    return request(
        "POST",
        "/product/category/path",
        body={"account": account, "catIds": [int(x) for x in cat_ids]},
        timeout=40,
    )


def product_template(account: str, cat_id: int, *, refresh: bool = False) -> dict:
    """类目模板（含全部属性定义，24h 缓存）。"""
    return request(
        "GET",
        "/product/template",
        params={"account": account, "catId": cat_id, "refresh": "1" if refresh else "0"},
        timeout=30,
    )


def product_describe(account: str, cat_id: int, selected: dict) -> dict:
    """按当前已选值返回应显示的字段、缺失项、颜色/尺码选项。"""
    return request(
        "POST",
        "/product/template/describe",
        body={"account": account, "catId": cat_id, "selected": selected or {}},
        timeout=30,
    )


def product_sites(account: str, *, refresh: bool = False) -> dict:
    return request(
        "GET",
        "/product/sites",
        params={"account": account, "refresh": "1" if refresh else "0"},
        timeout=30,
    )


def product_env(account: str, site_id: Optional[int] = None, *, refresh: bool = False) -> dict:
    """店铺环境：仓库、运费模板（1h 缓存）。"""
    params = {"account": account, "refresh": "1" if refresh else "0"}
    if site_id:
        params["siteId"] = site_id
    return request("GET", "/product/env", params=params, timeout=30)


def product_validate_size_template(
    account: str,
    cat_id: int,
    ids: list,
    general_size_type: int = 0,
    size_values: list | None = None,
) -> dict:
    """预检尺码模板：类目不符 / 类型不符 / 尺码覆盖不到（6000012 的三种成因）。"""
    params = {"account": account, "catId": cat_id, "ids": ",".join(str(x) for x in ids)}
    if general_size_type:
        params["generalSizeType"] = general_size_type
    if size_values:
        params["sizeValues"] = ",".join(str(x) for x in size_values)
    return request("GET", "/product/size-template/validate", params=params, timeout=60)


def product_folders(account: str) -> dict:
    """素材文件夹树。"""
    return request("GET", "/product/folders", params={"account": account}, timeout=40)


def product_remove_material(account: str, img_ids=None, video_ids=None) -> dict:
    """删除素材（批量下架）。"""
    return request(
        "POST",
        "/product/material/remove",
        body={
            "account": account,
            "imgIds": list(img_ids or []),
            "videoIds": list(video_ids or []),
        },
        timeout=60,
    )


def product_images(
    account: str,
    *,
    carousel: bool = False,
    page_no: int = 1,
    page_size: int = 20,
    name: Optional[str] = None,
    proportion: Optional[str] = None,
    material_type: Optional[int] = None,
    folder_id: int = 0,
) -> dict:
    """素材库列表。material_type: 0=视频 1=图片，None=图片（默认）。"""
    params = {
        "account": account,
        "carousel": "1" if carousel else "0",
        "pageNo": page_no,
        "pageSize": page_size,
        # ⚠️ 视频是 0，图片是 1；不传时 bridge 默认只给图片
        **({"type": material_type} if material_type is not None else {}),
        **({"folderId": folder_id} if folder_id else {}),
    }
    if name:
        params["name"] = name
    if proportion:
        params["proportion"] = proportion
    return request("GET", "/product/images", params=params, timeout=40)


def product_draft(account: str, cat_path: list, site_id: int) -> dict:
    return request(
        "POST",
        "/product/draft",
        body={"account": account, "catPath": cat_path, "siteId": site_id},
        timeout=40,
    )


def product_preview(account: str, form: dict) -> dict:
    """组装请求体但不提交（用于创建前确认）。"""
    return request("POST", "/product/preview", body={"account": account, "form": form}, timeout=60)


def product_create(account: str, form: dict, *, dry_run: bool = True) -> dict:
    """创建商品。dry_run=True 只走到 draft/save，不真正建品。"""
    return request(
        "POST",
        "/product/create",
        body={"account": account, "form": form, "dryRun": dry_run},
        timeout=180,
    )


def product_upload_video(account: str, content_base64: str, file_name: str = "upload.mp4") -> dict:
    """上传视频到素材中心（SDK 自动判分片 + 等转码）。"""
    return request(
        "POST",
        "/product/upload-video",
        body={"account": account, "contentBase64": content_base64, "fileName": file_name},
        timeout=300,
    )


def product_compress_image(account: str, urls: list) -> dict:
    """压缩图片（返回 compressMap: 原 url → 压缩后 url）。"""
    return request(
        "POST",
        "/product/material/compress",
        body={"account": account, "urls": list(urls)},
        timeout=120,
    )


def product_crop_image(account: str, url: str, cut_config: dict) -> dict:
    """服务端裁剪：把 CDN 图按 cutConfig 裁成新图。"""
    return request(
        "POST",
        "/product/material/crop",
        body={"account": account, "url": url, "cutConfig": cut_config},
        timeout=60,
    )


def product_upload(account: str, content_base64: str, file_name: str = "upload.jpg", crop=None) -> dict:
    """上传图片。crop=(width, height) 时服务端上传即裁切（pic_operations）。"""
    body = {"account": account, "contentBase64": content_base64, "fileName": file_name}
    if crop:
        body["cropWidth"] = int(crop[0])
        body["cropHeight"] = int(crop[1])
    return request("POST", "/product/upload", body=body, timeout=120)


def product_upload_many(account: str, files: list, concurrency: int = 4) -> dict:
    """批量上传图片。

    对齐官网 5 步流程：批量算 MD5 → 一次 queryByMd5 查重 → 分流 → 并发上传。
    返回 { uploaded, existList, failed }：
      uploaded  新上传的（含 imgUrl / materialId）
      existList 库里已有、直接复用（含 existId）
      failed    失败的（含 error）
    """
    return request(
        "POST",
        "/product/upload-many",
        body={"account": account, "files": files, "concurrency": int(concurrency)},
        timeout=600,
    )


def goods_lifecycle_filters(account: str) -> dict:
    """快捷筛选计数（生命周期 tab 上的数字）。"""
    return request("GET", "/goods/lifecycle/filters", params={"account": account}, timeout=30)


def goods_lifecycle_todo(account: str) -> dict:
    """待办计数。"""
    return request("GET", "/goods/lifecycle/todo", params={"account": account}, timeout=30)


def goods_lifecycle_all(account: str, *, page_size: int = 100) -> dict:
    """生命周期全量（bridge 侧自动翻页，最多 20 页）。

    超时压到 60 秒：这个接口一旦挂起会占住账号锁，把商品列表、客服请求一起拖死。
    """
    return request(
        "GET",
        "/goods/lifecycle/all",
        params={"account": account, "pageSize": page_size},
        timeout=60,
    )


def goods_detail(account: str, product_id: str) -> dict:
    return request("GET", "/goods/detail", params={"account": account, "productId": product_id})


def kefu_tools(account: str) -> dict:
    return request("GET", "/kefu/tools", params={"account": account})


def kefu_card(account: str, *, tool_id: Optional[str] = None, max_pages: int = 10) -> dict:
    """取工具卡片。

    卡片会被后续消息挤出最近 50 条窗口，bridge 侧会按 toolId 精确找并自动向前翻页。
    """
    return request(
        "GET",
        "/kefu/card",
        params={"account": account, "toolId": tool_id, "maxPages": max_pages},
    )


def kefu_cards(account: str, *, max_pages: int = 10) -> dict:
    """列出每个工具的最新卡片（按 toolId 去重）。"""
    return request("GET", "/kefu/cards", params={"account": account, "maxPages": max_pages})


def kefu_connect(account: str, *, send_robot_hot_faq: bool = True) -> dict:
    """建立/恢复客服会话。会话 off 时机器人不响应任何消息（发 QuickTool 也拿不到卡片）。"""
    return request(
        "POST",
        "/kefu/connect",
        body={"account": account, "sendRobotHotFaqMsg": send_robot_hot_faq},
        timeout=max(STORE_BRIDGE_TIMEOUT, 40),
    )


def kefu_wake(account: str, tool_name: str, *, tool_id: Optional[str] = None, timeout_ms: int = 30000) -> dict:
    """唤醒工具卡片：先恢复会话，再发 QuickTool（contentType=10），机器人推卡片。

    发纯文本不会唤醒卡片，必须用 QuickTool。
    """
    return request(
        "POST",
        "/kefu/wake",
        body={
            "account": account,
            "toolName": tool_name,
            "toolId": tool_id,
            "timeoutMs": timeout_ms,
        },
        timeout=max(STORE_BRIDGE_TIMEOUT, timeout_ms // 1000 + 25),
    )


def kefu_ensure(
    account: str,
    tool_name: str,
    *,
    tool_id: Optional[str] = None,
    max_age_ms: int = 600000,
    max_pages: int = 3,
    timeout_ms: int = 30000,
) -> dict:
    """确保有「新鲜」的工具卡片：够新直接复用，太旧就发工具名文本唤醒。

    返回里的 `source` 是 find（复用）或 wake（新推送）。
    """
    return request(
        "POST",
        "/kefu/ensure",
        body={
            "account": account,
            "toolName": tool_name,
            "toolId": tool_id,
            "maxAgeMs": max_age_ms,
            "maxPages": max_pages,
            "timeoutMs": timeout_ms,
        },
        timeout=max(STORE_BRIDGE_TIMEOUT, timeout_ms // 1000 + 20),
    )


def kefu_messages(account: str, *, limit: int = 50) -> dict:
    return request("GET", "/kefu/messages", params={"account": account, "limit": limit})


def kefu_query(account: str, data_type: str, data_id: str, tool_id: Optional[str] = None) -> dict:
    return request(
        "POST",
        "/kefu/query",
        body={"account": account, "type": data_type, "id": data_id, "toolId": tool_id},
    )


def kefu_intercept(account: str, data_id: str, tool_id: Optional[str] = None) -> dict:
    return request(
        "POST",
        "/kefu/intercept",
        body={"account": account, "id": data_id, "toolId": tool_id},
    )


def kefu_submit(account: str, payload: dict, *, dry_run: bool = True) -> dict:
    body = dict(payload)
    body["account"] = account
    body["dryRun"] = dry_run
    return request("POST", "/kefu/submit", body=body, timeout=STORE_LOGIN_TIMEOUT)


def kefu_reply(account: str, since_msg_id: Optional[str] = None) -> dict:
    return request("POST", "/kefu/reply", body={"account": account, "sinceMsgId": since_msg_id})
