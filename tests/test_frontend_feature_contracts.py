from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_followed_account_can_query_its_article_list() -> None:
    source = (ROOT / "frontend" / "src" / "views" / "TopicsView.vue").read_text(
        encoding="utf-8"
    )

    assert "async function queryFollowedArticles(account)" in source
    assert "articleFilters.account_ids = [account.id]" in source
    assert '@click="queryFollowedArticles(row)"' in source
    assert "查询文章</el-button>" in source
    assert "正在查看“${queriedAccount.name}”的文章列表" in source
    assert "backendSearchReady" in source
    assert "请先配置并保存公众号后台 Token 和 Cookie" in source
    assert "加密保存并继续" in source


def test_every_authenticated_user_sees_add_official_account_action() -> None:
    source = (ROOT / "frontend" / "src" / "views" / "SettingsView.vue").read_text(
        encoding="utf-8"
    )

    assert '@click="editAccount()">添加公众号</el-button>' in source
    assert 'v-if="isAdmin" type="primary" :icon="Plus" @click="editAccount()"' not in source
    assert "api.configurationAccounts()" in source
    assert "api.saveAccount({" in source
