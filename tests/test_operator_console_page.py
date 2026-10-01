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
    assert "SSH receiver " in page
    assert "pinned " in page
    assert "Profiles · " in page
    assert "Capabilities · " in page
    assert "placement_candidates" in page

def test_operator_console_compacts_account_browser_controls_on_mobile():
    page = operator_console_document()

    assert ".account-actions button{flex:0 0 auto!important" in page
    assert ".account-login-dialog>.row:first-child>button{flex:0 0 auto!important" in page
    assert ".browser-controls>.row>button{flex:0 0 auto!important" in page
    assert ".browser-keys button{flex:0 0 auto!important" in page
    assert ".account-login-dialog{width:100%;max-height:calc(100dvh - 16px)" in page
    assert ".account-login-dialog h3+p{display:none}" in page
    assert ".browser-live{border-radius:6px;max-height:52dvh;object-fit:contain}" in page


def test_live_browser_status_never_renders_full_oauth_url_inline():
    page = operator_console_document()

    assert "function shortBrowserLocation(url)" in page
    assert "status.title=fmt(msg.url)" in page
    assert "status.textContent=prefix+(host?' · '+host:'')+(title?' · '+title:'')" in page
    assert "status.textContent=prefix+' · '+fmt(msg.url)" not in page
    assert "white-space:nowrap;overflow:hidden;text-overflow:ellipsis" in page


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
    assert "Browser Use 通过本机 CDP 复用它" in page
    assert "当前兼容 fallback：" in page
    assert "创建独立浏览器环境" in page
    assert "测试该账号 Browser Use" in page
    assert "网页登录（网页自动化）" not in page


def test_accounts_ui_supports_multiple_x_accounts_and_headless_oauth():
    page = operator_console_document()

    assert 'id="addXAccount"' in page
    assert 'id="xOAuthShell"' in page
    assert "添加 X 账号" in page
    assert "/operator/api/accounts/x/oauth/start" in page
    assert "/operator/api/accounts/x/oauth/complete" in page
    assert "每个账号拥有独立 Chrome profile/cookie" in page
    assert "测试该账号 Browser Use" in page
    assert "调用 X API 时显式使用该账号" in page


def test_operator_login_page_offers_mobile_friendly_one_time_code_delivery():
    page = operator_console_document()

    assert 'id="emailSignin"' in page
    assert 'id="wecomSignin"' in page
    assert "8 位一次性登录码" in page
    assert 'id="token"' in page
    assert 'autocomplete="one-time-code"' in page
    assert 'autocapitalize="characters"' in page
    assert 'placeholder="8 位登录码，例如 ABCD-EFGH"' in page
    assert "自动忽略空格、连字符和大小写" in page
    assert "el('token').onkeydown" in page
    assert "/operator/api/request-login" in page
    assert "/operator/api/bootstrap" not in page
    assert "#bootstrap=" not in page
    assert 'id="operatorEmailRecipient"' in page
    assert "/operator/api/login-delivery/email" in page


def test_account_cards_offer_confirmed_delete_action():
    page = operator_console_document()

    assert "删除账户" in page
    assert "confirm_identity" in page
    assert "account_delete_confirmation_mismatch" not in page
    assert "method:'DELETE'" in page
    assert "button.danger" in page


def test_x_account_card_offers_account_scoped_web_login():
    page = operator_console_document()

    assert "登录 / 更换网页会话" in page
    assert "不再启动第二套浏览器" in page
    assert "持久网页登录" in page
    assert page.index("登录 / 更换网页会话") < page.index("高级操作 / 技术信息")
    assert "/operator/api/account-instances/" in page
    assert "/browser/browser-use-test" in page
    assert "/browser/login/connect" in page
    assert "/browser/login/stream" in page
    assert "/browser/login/stop" in page


def test_jev_card_offers_chatgpt_cortex_login():
    page = operator_console_document()

    assert "登录 ChatGPT Cortex" in page
    assert "ChatGPT Cortex · 持久网页登录" in page
    assert "正在启动 ChatGPT 专用持久 Chrome profile" in page
    assert "/operator/api/cortex/browser/login/connect" in page
    assert "/operator/api/cortex/browser/login/stream" in page
    assert "/operator/api/cortex/browser/login/stop" in page
    assert "网页激活只发送 request_id，不读取模型输出" in page
    assert "与 X 登录完全独立" in page
    assert "activeAccountProvider='chatgpt-cortex';activeAccountId='';selectPanel('accounts')" in page
    assert "beginCortexLogin().catch" in page
    assert 'id="cortexOtherMethod"' in page
    assert "type:'click_text',text:'Try another method'" in page


def test_operator_console_restores_session_without_using_heavy_status_as_auth_probe():
    page = operator_console_document()

    assert "async function restoreSession()" in page
    assert "await api('/operator/api/session',{method:'GET'})" in page
    assert "restoreSession()" in page
    assert "refresh().catch(()=>showLogin())" not in page
    assert "if(e.status===401)return" in page
    assert "后台状态加载失败" in page


def test_operator_console_never_shows_an_empty_dashboard_while_status_loads():
    page = operator_console_document()

    assert "正在加载后台状态" in page
    assert "正在加载资源状态" in page
    assert "metricCard('会话','已登录','正在加载后台状态')" in page
