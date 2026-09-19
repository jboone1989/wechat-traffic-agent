from wechat_traffic_agent.channels.operator_console_page import operator_console_document


def test_operator_console_uses_admin_information_architecture():
    page = operator_console_document()

    assert 'data-panel="dashboard"' in page
    assert 'data-panel="todos"' in page
    assert 'data-panel="accounts"' in page
    assert 'data-panel="resources"' in page
    assert 'data-panel="config"' in page
    assert "仪表盘" in page
    assert "待办事项" in page
    assert "资源管理" in page
    assert "配置管理" in page
    assert 'id="dashboardHealth"' in page
    assert 'id="dashboardActivity"' in page
    assert "@media(max-width:760px)" in page


def test_operator_console_keeps_existing_admin_controls():
    page = operator_console_document()

    assert "添加服务器" in page
    assert 'id="sshHost"' in page
    assert 'id="sshPassword"' in page
    assert 'id="bootstrapHost"' in page
    assert 'id="secrets"' in page
    assert 'id="accounts"' in page
    assert 'id="accountLoginShell"' in page
    assert "/operator/api/accounts/" in page
    assert 'id="localBrowserStart"' not in page
    assert 'href="/operator/chat/"' in page

def test_operator_console_compacts_account_browser_controls_on_mobile():
    page = operator_console_document()

    assert ".account-actions button{flex:0 0 auto!important" in page
    assert ".account-login-dialog>.row:first-child>button{flex:0 0 auto!important" in page
    assert ".browser-controls>.row>button{flex:0 0 auto!important" in page
    assert ".browser-keys button{flex:0 0 auto!important" in page
    assert ".account-login-dialog{width:100%;max-height:calc(100dvh - 16px)" in page


def test_browser_login_uses_modal_overlay_instead_of_inline_expansion():
    page = operator_console_document()

    assert 'id="accountLoginShell" class="account-login-modal hidden"' in page
    assert 'class="account-login-dialog"' in page
    assert 'role="dialog"' in page
    assert 'aria-modal="true"' in page
    assert ".account-login-modal{position:fixed;inset:0;z-index:100" in page
    assert "document.body.classList.add('modal-open')" in page
    assert "document.body.classList.remove('modal-open')" in page

def test_x_account_ui_uses_oauth_and_tinyfish_without_requiring_local_browser_login():
    page = operator_console_document()

    assert "API 连接" in page
    assert "网页备用" in page
    assert "TinyFish 已保持 X 登录态" in page
    assert "你无需再登录服务器浏览器" in page
    assert "高级操作 / 技术信息" in page
    assert "登录本地服务器浏览器" in page
    assert "检查本地网页登录" in page
    assert "Fallback: TinyFish → Local CDP" in page
    assert "网页登录（网页自动化）" not in page
