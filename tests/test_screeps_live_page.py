from wechat_traffic_agent.channels.screeps_live_page import screeps_live_document
def test_static_screeps_feed_url():
 html=screeps_live_document(data_url="/screeps/live.json")
 assert "fetch('/screeps/live.json'" in html
 assert "rawResearch.arm" in html
 assert "reward pending" in html
 from wechat_traffic_agent.site_audit import _PageParser
 parser = _PageParser()
 parser.feed(html)
 assert parser.has_title
 assert parser.has_description
 assert parser.has_canonical
 assert 'href="https://livingruntime.com/screeps/"' in html
 assert "scheduler_status" in html
 assert "'LIVE'" in html
