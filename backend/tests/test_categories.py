"""品类按区域开放的回归测试。"""
import pytest

from app.services.categories import create_category, delete_category, list_categories, update_category
from app.services.regions import create_region


def _ensure_region(code: str):
    try:
        create_region({"code": code, "name": f"{code}测试区", "currency": "CNY", "copy_from": "US"})
    except ValueError:
        pass


def _codes(items):
    return [item["code"] for item in items]


def test_category_allowed_regions_filtering():
    _ensure_region("GBTEST")
    created = create_category({"code": "REGTEST", "name": "区域测试品类", "template_type": "no_set", "allowed_regions": ["US"]})
    try:
        assert created["allowed_regions"] == ["US"]
        # 仅对开放区域可见
        assert "REGTEST" in _codes(list_categories(region_code="US"))
        assert "REGTEST" not in _codes(list_categories(region_code="GBTEST"))
        # 不带区域过滤（管理端视角）时全部可见
        assert "REGTEST" in _codes(list_categories())
    finally:
        delete_category("REGTEST")


def test_category_all_regions_by_default():
    _ensure_region("GBTEST")
    created = create_category({"code": "ALLREG", "name": "全开放品类", "template_type": "no_set"})
    try:
        assert created["allowed_regions"] is None
        assert "ALLREG" in _codes(list_categories(region_code="US"))
        assert "ALLREG" in _codes(list_categories(region_code="GBTEST"))
    finally:
        delete_category("ALLREG")


def test_category_update_allowed_regions():
    _ensure_region("GBTEST")
    create_category({"code": "REGUPD", "name": "更新区域品类", "template_type": "no_set"})
    try:
        updated = update_category("REGUPD", {"name": "更新区域品类", "template_type": "no_set", "allowed_regions": ["GBTEST"], "enabled": True, "is_default": False})
        assert updated["allowed_regions"] == ["GBTEST"]
        assert "REGUPD" not in _codes(list_categories(region_code="US"))
        assert "REGUPD" in _codes(list_categories(region_code="GBTEST"))
        # 清回全部开放
        updated = update_category("REGUPD", {"name": "更新区域品类", "template_type": "no_set", "allowed_regions": None, "enabled": True, "is_default": False})
        assert updated["allowed_regions"] is None
        assert "REGUPD" in _codes(list_categories(region_code="US"))
    finally:
        delete_category("REGUPD")


def test_category_rejects_unknown_region():
    with pytest.raises(ValueError, match="区域不存在"):
        create_category({"code": "BADREG", "name": "非法区域品类", "template_type": "no_set", "allowed_regions": ["XX"]})
