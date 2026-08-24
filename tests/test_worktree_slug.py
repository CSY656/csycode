"""T1: Slug 校验单测。"""

import pytest
from csycode.worktree.slug import validate_slug, flatten_slug, flat_slug


class TestValidateSlug:
    """validate_slug 合法性校验（对齐 mewcode: 返回 str|None）。"""

    # ── 合法 case ──
    @pytest.mark.parametrize(
        "name",
        [
            "alice",
            "team/alice",
            "v1.0",
            "a_b",
            "a-b",
            "feature/my-branch_v2",
            "x" * 64,  # 正好 64 字符
        ],
    )
    def test_valid_slugs(self, name: str) -> None:
        """合法 slug 返回 None。"""
        assert validate_slug(name) is None

    # ── 非法 case ──
    @pytest.mark.parametrize(
        "name",
        [
            "",
            "x" * 65,
            "..",
            "../etc",
            "./x",
            "a//b",
            "/x",
            "a/",
            "a b",
            "a;b",
            "a|b",
            "a\tb",
            ".",
            "a/.",
            "a/..",
        ],
    )
    def test_invalid_slugs(self, name: str) -> None:
        """非法 slug 返回非空错误字符串。"""
        err = validate_slug(name)
        assert err is not None
        assert len(err) > 0


class TestFlatSlug:
    """flatten_slug 转换。"""

    def test_simple(self) -> None:
        assert flatten_slug("alice") == "alice"

    def test_nested(self) -> None:
        assert flatten_slug("team/alice") == "team+alice"

    def test_already_flat(self) -> None:
        assert flatten_slug("team+alice") == "team+alice"

    def test_no_slash(self) -> None:
        assert flatten_slug("a-b_c.v1") == "a-b_c.v1"

    def test_flat_slug_alias(self) -> None:
        """flat_slug 是 flatten_slug 的别名。"""
        assert flat_slug("team/alice") == "team+alice"
