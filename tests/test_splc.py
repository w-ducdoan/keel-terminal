from pmdesk import splc

IDX = {"apple": ("AAPL", 320193), "walmart": ("WMT", 104169), "att": ("T", 732717), "general electric": ("GE", 40545),
       "research frontiers": ("REFR", 1), "skyworks solutions": ("SWKS", 4127)}


def test_resolve_exact_and_prefix():
    assert splc.resolve("Apple Inc.", IDX)[0] == "AAPL"
    assert splc.resolve("Walmart Stores Inc", IDX)[0] == "WMT"      # formal name: prefix on whole words
    assert splc.resolve("General", IDX) == (None, None)              # common word alone never matches
    assert splc.resolve("Research", IDX) == (None, None)
    assert splc.resolve("Change October", IDX, exact=True) == (None, None)


def test_classify_relationship_from_the_filer_side():
    assert splc._classify("We use Toast, Inc. software for our point of sale.") == "filer_buys"   # target supplies the filer
    assert splc._classify("Our customers include Valmont Industries, Inc.") == "filer_sells"       # target buys from the filer
    assert splc._classify("Our major CRO competitors include IQVIA Holdings Inc.") == "competitor"


def test_noise_sentences_are_not_relationships():
    for s in ["Mr. Smith previously served as CFO of IQVIA Holdings Inc.",
              "He was with Wolfson (now Cirrus Logic) as a director.",
              "The peer group includes Cintas Corporation and MasTec, Inc."]:
        assert splc.NOISE.search(s)


def test_concentration_sentence_patterns():
    s1 = ("For fiscal years 2026, 2025, and 2024, we had one end customer, Apple Inc., who purchased through multiple contract "
          "manufacturers and represented approximately 91 percent, 89 percent, and 87 percent, of the Company's total net sales, respectively.")
    s2 = "Sales to Walmart Inc. and its affiliates represent approximately 16% of our total sales in 2026, 2025 and 2024."
    for s in (s1, s2):
        assert splc.CONC_VERB.search(s) and splc.PCT_OF_SALES.search(s) and not splc.CONC_SKIP.search(s)
    assert splc.PCT_OF_SALES.search(s1).group(1) == "91" and splc.PCT_OF_SALES.search(s2).group(1) == "16"
    skips = ["Our 50 largest customers accounted for approximately 12% of our net sales.",
             "Selling, general and administrative expenses represented 18% of revenue, an increase from 17%.",
             "Our natural gas services represented $1.6 billion, or 11% of our revenue.",
             "Miller Electric constituted 4.4% of total assets and 6.4% of revenues; internal control excluded it."]
    for s in skips:
        assert splc.CONC_SKIP.search(s), s


def test_short_name():
    assert splc.short_name("VALMONT INDUSTRIES INC") == "Valmont Industries"
    assert splc.short_name("Toast, Inc.") == "Toast"


def test_name_pattern_needs_company_capitalization():
    rx = splc.name_pattern("On Holding")
    assert rx.search("Shoes from On Holding AG were sold") and not rx.search("we rely on holding cash")
    assert splc.name_pattern("Nvidia").search("GPUs from NVIDIA Corporation")
    assert splc.name_pattern("Toast Inc").search("software from Toast, Inc. for payroll")


def test_competition_and_capital_deals_are_not_supply_links():
    assert splc._classify("Nvidia's practices can limit customers' ability to choose non-Nvidia products, including ours.") == "competitor"
    assert splc._classify("On March 31 we issued and sold to NVIDIA 2,000,000 shares of preferred stock to our customer base.") == "mention"


def test_licensee_counts_as_customer():
    assert splc._classify("Our publicly announced licensees include Acme and Cirrus Logic.") == "filer_sells"
