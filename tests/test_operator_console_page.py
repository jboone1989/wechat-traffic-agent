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

def test_x_account_ui_uses_account_scoped_oauth_and_jev():
    page = operator_console_document()

    assert "官方 API" in page
    assert "网页自动化" in page
    assert "OAuth 身份独立绑定到这个账号" in page
    assert "长期执行器：" in page
    assert "每个账号拥有独立 Chrome profile/cookie" in page
    assert "当前兼容 fallback：" in page
    assert "创建独立浏览器环境" in page
    assert "测试该账号 Jev" in page
    assert "网页登录（网页自动化）" not in page


def test_accounts_ui_supports_multiple_x_accounts_and_headless_oauth():
    page = operator_console_document()

    assert 'id="addXAccount"' in page
    assert 'id="xOAuthShell"' in page
    assert "添加 X 账号" in page
    assert "/operator/api/accounts/x/oauth/start" in page
    assert "/operator/api/accounts/x/oauth/complete" in page
    assert "每个账号拥有独立 Chrome profile/cookie" in page
    assert "测试该账号 Jev" in page
    assert "调用 X API 时显式使用该账号" in page


def test_operator_login_page_offers_email_delivery_and_config():
    page = operator_console_document()

    assert 'id="emailSignin"' in page
    assert "发送到 Email" in page
    assert 'id="operatorEmailProvider"' in page
    assert 'id="operatorEmailRecipient"' in page
    assert 'id="operatorEmailSender"' in page
    assert "/operator/api/login-delivery/email" in page
    assert "RESEND_API_KEY" not in page
    assert "CONTENT_OPERATOR_SMTP_PASSWORD" not in page
