from pmdesk import events_feed as ev


def test_parse_name():
    assert ev.parse_name("TOAST, INC.  (TOST)  (CIK 0001650164)") == {"name": "TOAST, INC.", "ticker": "TOST", "cik": 1650164}
    assert ev.parse_name("Compass Diversified Holdings  (CODI, CODI-PA)  (CIK 0001345126)")["ticker"] == "CODI"
    assert ev.parse_name("Merck KGaA  (CIK 0001764133)") == {"name": "Merck KGaA", "ticker": None, "cik": 1764133}


def test_classify_rules():
    t = ev.classify("SC TO-T", ["RAPID MICRO BIOSYSTEMS, INC.  (RPID)  (CIK 0001380106)", "Merck KGaA  (CIK 0001764133)"])
    assert t["type"] == "tender" and t["ticker"] == "RPID" and t["counterparty"] == "Merck KGaA"
    assert ev.classify("SCHEDULE 13D/A", ["X CORP  (XX)  (CIK 0000000001)"]) is None             # amendments skipped
    assert ev.classify("SC TO-I", ["Fortress Private Lending Fund  (CIK 0002012139)"]) is None    # private fund
    assert ev.classify("DEFM14A", ["Alchemy Investments Acquisition Corp 1  (ALCUF)  (CIK 0001901336)"]) is None  # SPAC
    assert ev.classify("PREM14A", ["Some Co  (CIK 0000000002)"]) is None                          # no ticker
    assert ev.classify("10-12B", ["Trinzic, Inc.  (CIK 0002101664)"])["type"] == "spinoff"         # spin-offs have none yet
