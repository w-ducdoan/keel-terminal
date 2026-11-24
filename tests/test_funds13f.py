from pmdesk import funds13f as f

XML = """<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable><nameOfIssuer>ALPHABET INC</nameOfIssuer><titleOfClass>CAP STK CL A</titleOfClass><cusip>02079K305</cusip>
    <value>1000000</value><shrsOrPrnAmt><sshPrnamt>4000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
  <infoTable><nameOfIssuer>ALPHABET INC</nameOfIssuer><titleOfClass>CAP STK CL C</titleOfClass><cusip>02079K107</cusip>
    <value>500000</value><shrsOrPrnAmt><sshPrnamt>2000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
  <infoTable><nameOfIssuer>TOAST INC</nameOfIssuer><titleOfClass>CL A</titleOfClass><cusip>888787108</cusip>
    <value>300000</value><shrsOrPrnAmt><sshPrnamt>9000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><putCall>Put</putCall></infoTable>
</informationTable>"""


def test_parse_and_aggregate_share_classes_skip_options():
    rows = f.parse_infotable(XML)
    assert len(rows) == 2                                   # the put line is skipped
    agg = f.aggregate(rows)
    assert agg["ALPHABET"]["shares"] == 6000 and agg["ALPHABET"]["value"] == 1_500_000


def test_norm_matches_sec_and_13f_spellings():
    assert f.norm("Toast, Inc.") == f.norm("TOAST INC CL A") == "TOAST"
    assert f.norm("Alphabet Inc.") == f.norm("ALPHABET INC CAP STK CL C") == "ALPHABET"
    assert f.norm("CoStar Group, Inc.") == f.norm("COSTAR GROUP INC") == "COSTAR"


def test_change_classification():
    assert f.change({"shares": 10}, None)["action"] == "new"
    assert f.change(None, {"shares": 10})["action"] == "exited"
    assert f.change({"shares": 15}, {"shares": 10})["action"] == "added"
    assert f.change({"shares": 10.1}, {"shares": 10})["action"] == "held"
    assert f.change({"shares": 5}, {"shares": 10})["action"] == "trimmed"


def test_parse_company_feed_list_and_single():
    many = """<feed><title>Company Search Feed</title>
      <entry><content><company-info><cik>0001649339</cik></company-info></content><title>Scion Asset Management, LLC</title></entry>
      <entry><content><company-info><cik>0001182422</cik></company-info></content><title>SCION CAPITAL LLC</title></entry></feed>"""
    assert [(x["name"], x["cik"]) for x in f.parse_company_feed(many)] == [("Scion Asset Management, LLC", 1649339), ("SCION CAPITAL LLC", 1182422)]
    one = "<feed><company-info><cik>0001079114</cik><conformed-name>GREENLIGHT CAPITAL INC</conformed-name></company-info><entry><title>13F-HR</title></entry></feed>"
    assert f.parse_company_feed(one) == [{"name": "GREENLIGHT CAPITAL INC", "cik": 1079114}]
