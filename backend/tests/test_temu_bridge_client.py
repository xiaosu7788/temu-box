"""bridge 客户端的连线测试：验证每个包装函数都能被调用（不联网）。

这组用例的目标是「名字/参数级别」的错误：曾经的 500 就是
`kefu_submit` 里用了未导入的 `STORE_LOGIN_TIMEOUT`，而业务测试把整个
`kefu_submit` 打了桩，导致 NameError 逃过测试。
"""
import pytest

from app.services import temu_bridge


@pytest.fixture()
def captured(monkeypatch):
    calls = []

    def fake_request(method, path, *, params=None, body=None, timeout=None):
        calls.append({"method": method, "path": path, "params": params, "body": body, "timeout": timeout})
        return {"ok": True}

    monkeypatch.setattr(temu_bridge, "request", fake_request)
    monkeypatch.setattr(temu_bridge, "configured", lambda: True)
    return calls


def test_login_passes_login_timeout(captured):
    temu_bridge.login("a@example.com", "secret", "100001")
    call = captured[0]
    assert call["path"] == "/session/login"
    assert call["body"] == {"account": "a@example.com", "password": "secret", "mallId": "100001"}
    assert call["timeout"] and call["timeout"] >= 10


def test_kefu_submit_forces_dry_run_flag_and_timeout(captured):
    temu_bridge.kefu_submit("b@example.com", {"toolId": "2406230000031"}, dry_run=False)
    call = captured[0]
    assert call["path"] == "/kefu/submit"
    assert call["body"]["account"] == "b@example.com"
    assert call["body"]["dryRun"] is False
    assert call["timeout"] and call["timeout"] > 10


def test_kefu_readonly_helpers_shape_the_query(captured):
    temu_bridge.kefu_tools("b@example.com")
    temu_bridge.kefu_messages("b@example.com", limit=20)
    temu_bridge.kefu_query("b@example.com", "SKC", "418931287377", "2406230000031")
    temu_bridge.kefu_intercept("b@example.com", "418931287377")
    temu_bridge.kefu_reply("b@example.com", "638869614600999")

    paths = [call["path"] for call in captured]
    assert paths == ["/kefu/tools", "/kefu/messages", "/kefu/query", "/kefu/intercept", "/kefu/reply"]
    assert captured[1]["params"] == {"account": "b@example.com", "limit": 20}
    assert captured[2]["body"] == {"account": "b@example.com", "type": "SKC", "id": "418931287377", "toolId": "2406230000031"}
    assert captured[3]["body"] == {"account": "b@example.com", "id": "418931287377", "toolId": None}
    assert captured[4]["body"] == {"account": "b@example.com", "sinceMsgId": "638869614600999"}


def test_goods_helpers_pass_bind_site_ids(captured):
    temu_bridge.goods_list("b@example.com", page=2, page_size=50, bind_site_ids="100")
    temu_bridge.goods_detail("b@example.com", "9001")
    temu_bridge.goods_sites("b@example.com", force=True)

    assert captured[0]["params"] == {
        "account": "b@example.com",
        "page": 2,
        "pageSize": 50,
        "bindSiteIds": "100",
        "site": None,
        "skcSiteStatus": None,
        "productName": None,
        "productSkcIds": None,
    }
    assert captured[1]["params"] == {"account": "b@example.com", "productId": "9001"}
    assert captured[2]["params"] == {"account": "b@example.com", "force": "true"}


def test_goods_list_passes_skc_site_status(captured):
    """skcSiteStatus 是唯一有效的状态筛选参数（0 未发布 / 1 已上架）。"""
    temu_bridge.goods_list("b@example.com", skc_site_status=0)
    assert captured[0]["params"]["skcSiteStatus"] == 0


def test_missing_configuration_raises_bridge_error(monkeypatch):
    monkeypatch.setattr(temu_bridge, "configured", lambda: False)
    with pytest.raises(temu_bridge.BridgeError) as exc:
        temu_bridge.kefu_tools("b@example.com")
    assert exc.value.code == "NOT_CONFIGURED"
