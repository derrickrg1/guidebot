"""Questions an AE or underwriter would ask, with what a right answer has to do. Used by tools/stress_run.py.

Each case: id, section, program ("" = all in the tab), mode ("ask" Guidelines / "deal" Scenario), q, optional
history (earlier turns), and expect:
  route    "deal" (matrix lookup), "guide" (guideline answer), "pick" (asks Pick a program / Review all),
           "missing" (asks for the deal facts it lacks)
  deal     the facts the question states, checked against the answer's own echo of the deal
  ltv      the matrix cell the deal must land on (Conventional, hand-read from the PDFs); NonQM cells are worked
           out independently from the approved grids by the runner
  eligible False when the deal must come back ineligible
  must / must_not   regexes the answer has to contain / must not contain
  cite     a regex the cited file names must match (the right source)
"""

P, R, C = "Purchase", "Rate/Term Refinance", "Cash Out Refinance"
PRI, SEC, INV = "Primary Residence", "Second Home", "Investment Property"


def conv_deals():
    # (program, question, expect) hand-read from the NDM Conventional sheets dated 09.17.2026
    F, D = "Conventional Fannie Mae", "Conventional Freddie Mac"
    rows = [
        (F, "680 FICO, $500k cash-out refi, primary, single family", dict(ltv=80)),
        (F, "720 FICO, $400k purchase, primary, SFR", dict(ltv=97)),
        (F, "700 FICO, $1.2M purchase, primary, SFR", dict(ltv=95)),
        (F, "700 FICO, $1.5M purchase, primary, duplex", dict(ltv=85)),
        (F, "680 FICO, $350k limited cash-out refi, investment, fourplex", dict(ltv=75)),
        (F, "700 FICO, $600k cash-out, investment, 3 units", dict(ltv=70)),
        (F, "700 FICO, $300k cash-out, second home, manufactured home", dict(eligible=False)),
        (F, "640 FICO, $300k purchase, investment, 3 units", dict(ltv=75)),
        (F, "690 FICO, $450k purchase, second home", dict(ltv=90)),
        (F, "690 FICO, $450k cash out, 2nd home", dict(ltv=75)),
        (F, "700 FICO, $500k purchase, investment property, single family", dict(ltv=85)),
        (F, "619 FICO, $300k purchase, primary", dict(eligible=False)),
        (F, "700 FICO, $845,000 purchase, primary, SFR", dict(ltv=97)),
        (F, "700 FICO, $845,001 purchase, primary, SFR", dict(ltv=95)),
        (F, "700 FICO, $1.3M purchase, primary, SFR", dict(eligible=False)),
        (F, "700 FICO, $250k cash-out, primary, manufactured", dict(ltv=65)),
        (F, "720 FICO, $1.0M rate and term refi, primary, triplex", dict(ltv=95)),   # 3-4 unit primary row, 95%
        (D, "700 FICO, $1.1M purchase, primary, 2-unit", dict(ltv=85)),
        (D, "700 FICO, $1.4M purchase, primary, triplex", dict(ltv=80)),
        (D, "690 FICO, $250k cash-out, primary, manufactured home", dict(ltv=65)),
        (D, "700 FICO, $900k cash-out, primary, manufactured home", dict(eligible=False)),
        (D, "700 FICO, $2.5M purchase, primary, fourplex", dict(eligible=False)),
        (D, "700 FICO, $500k purchase, second home", dict(ltv=90)),
        (D, "660 FICO, $400k no cash-out refi, investment, duplex", dict(ltv=75)),
        (D, "660 FICO, $400k cash-out refi, investment, duplex", dict(ltv=70)),
        ("Conventional Home Possible", "660 FICO, $320k purchase, primary, 2 units", dict(ltv=95)),
        ("Conventional Home Possible", "700 FICO, $500k purchase, investment", dict(eligible=False)),
        ("Conventional HomeReady", "700 FICO, $1M purchase, primary, SFR", dict(ltv=95)),
        ("Conventional HomeReady", "700 FICO, $400k cash-out, primary", dict(eligible=False)),
        ("Conventional HomeOne", "700 FICO, $500k purchase, primary, 2 units", dict(eligible=False)),
        ("Conventional HomeOne", "700 FICO, $500k purchase, primary, SFR", dict(ltv=97)),
        ("Conventional Refi Possible", "680 FICO, $400k rate/term refi, primary, SFR", dict(ltv=97)),
        ("Conventional RefiNow", "700 FICO, $400k cash-out refi, primary", dict(eligible=False)),
        ("Conventional RefiNow", "700 FICO, $300k no cash out refi, primary, manufactured home", dict(ltv=95)),
    ]
    out = []
    for i, (prog, q, exp) in enumerate(rows):
        out.append(dict(id=f"conv-deal-{i:02d}", section="Conventional", program=prog, mode="deal", q=q,
                        expect=dict(route="deal", **exp)))
    return out


def phrasing():
    """The same kinds of deal said the way people actually type them. The deal facts must be read right."""
    F = "Conventional Fannie Mae"
    rows = [
        ("Ignite 1099", "borrower has a 712 score, buying a primary for $850k with 20% down",
         dict(fico=712, loan=680000, occ=PRI, purpose=P)),
        ("Ignite 1099", "mid score 705, $1.2M loan, owner occupied, cash out",
         dict(fico=705, loan=1200000, occ=PRI, purpose=C)),
        ("Ignite Bank Statements", "fico 690 / 2nd home / R/T refi / loan amt 640,000",
         dict(fico=690, loan=640000, occ=SEC, purpose=R)),
        ("Ignite Bank Statements", "NOO purchase, 725 credit, 1.1m", dict(fico=725, loan=1100000, occ=INV, purpose=P)),
        ("Ignite Full Doc", "value is $1M, they want a $650k cash-out refi on their primary, FICO 681",
         dict(fico=681, loan=650000, occ=PRI, purpose=C)),
        ("Ignite Full Doc", "vacation home purchase, 740 FICO, purchase price 2,000,000, 25% down",
         dict(fico=740, loan=1500000, occ=SEC, purpose=P)),
        ("Ignite P&L", "rental property refi, rate and term, 702 fico, $480,000", dict(fico=702, loan=480000, occ=INV, purpose=R)),
        ("Express Bank Statements", "Primary purchase 699 FICO 900K", dict(fico=699, loan=900000, occ=PRI, purpose=P)),
        ("Express 1099", "c/o refi primary 745 FICO 1.75M", dict(fico=745, loan=1750000, occ=PRI, purpose=C)),
        ("Express Full Doc", "investment purchase $2,250,000 loan 760 credit", dict(fico=760, loan=2250000, occ=INV, purpose=P)),
        (F, "buying a duplex to live in, 704 fico, $640k loan", dict(fico=704, loan=640000, occ=PRI, purpose=P, units=2)),
        (F, "owner occupied fourplex purchase, 701 fico, $780k loan", dict(fico=701, loan=780000, occ=PRI, purpose=P, units=4)),
        (F, "700 FICO, $400k purchase, primary, condo", dict(fico=700, loan=400000, occ=PRI, purpose=P, units=1)),
    ]
    return [dict(id=f"phrase-{i:02d}", section="Conventional" if p.startswith("Conventional") else "NonQM",
                 program=p, mode="deal", q=q, expect=dict(route="deal", deal=d)) for i, (p, q, d) in enumerate(rows)]


def nonqm_deals():
    """Grid deals across every NonQM program; the runner works out each expected cell from the approved grid."""
    cases = []
    progs = ["Ignite 1099", "Ignite Bank Statements", "Ignite Full Doc", "Ignite P&L", "Express 1099",
             "Express Bank Statements", "Express Full Doc", "Express P&L"]
    combos = [(720, 750000, PRI, P), (700, 1250000, PRI, C), (680, 1800000, SEC, R), (660, 900000, INV, P),
              (640, 600000, PRI, C), (705, 2400000, INV, C), (725, 3200000, PRI, P), (690, 1000000, PRI, P),
              (690, 1000001, PRI, P), (619, 500000, PRI, P)]
    words = {PRI: "primary", SEC: "second home", INV: "investment", P: "purchase", R: "rate and term refi",
             C: "cash-out refi"}
    for prog in progs:
        for j, (fico, loan, occ, pur) in enumerate(combos):
            q = f"{fico} FICO, ${loan:,} loan, {words[occ]}, {words[pur]}"
            cases.append(dict(id=f"nq-{prog.replace(' ', '').replace('&', '')}-{j}", section="NonQM", program=prog,
                              mode="deal", q=q, expect=dict(route="deal", deal=dict(fico=fico, loan=loan, occ=occ,
                                                                                    purpose=pur))))
    for j, (fico, loan, pur, dscr) in enumerate([(720, 800000, P, 1.25), (700, 1200000, C, 1.1), (680, 600000, R, 0.85),
                                                 (660, 900000, P, 0.9), (740, 3700000, P, 1.3), (700, 2200000, C, 0.8)]):
        for prog in ("Ignite Express DSCR", "Express DSCR"):
            q = f"DSCR {dscr}, {fico} FICO, ${loan:,} loan, investment {words[pur]}"
            cases.append(dict(id=f"dscr-{prog.split()[0]}-{j}", section="NonQM", program=prog, mode="deal", q=q,
                              expect=dict(route="deal", deal=dict(fico=fico, loan=loan, occ=INV, purpose=pur, dscr=dscr))))
    return cases


def routing():
    """Is it a deal, a rule question, or does it need more? Several of these broke before."""
    rows = [
        ("Conventional", "", "deal", "Are temporary buydowns allowed on a cash-out refi?", "guide", []),
        ("Conventional", "", "ask", "Are temporary buydowns allowed on a cash-out refi?", "guide", []),
        ("Conventional", "", "ask", "700 FICO, $450k purchase, primary, SFR", "pick", []),
        ("Conventional", "", "deal", "700 FICO, $450k purchase, primary, SFR", "deal", []),
        ("Conventional", "", "deal", "Freddie Mac: 700 FICO, $1.4M purchase, primary, triplex", "deal", []),
        ("Conventional", "", "deal", "Fannie Mae: 700 FICO, purchase, primary", "missing",
         [{"role": "user", "content": "Freddie Mac: 700 FICO, $2.5M purchase, primary, fourplex"},
          {"role": "assistant", "content": "**Not eligible on this matrix**"}]),
        ("Conventional", "Conventional Fannie Mae", "deal", "what if it's a cash-out instead?", "deal",
         [{"role": "user", "content": "720 FICO, $400k purchase, primary, SFR"},
          {"role": "assistant", "content": "**Max LTV/CLTV: 97%**"}]),
        ("Conventional", "Conventional Fannie Mae", "deal", "Is a cash-out allowed on a second home?", "guide",
         [{"role": "user", "content": "720 FICO, $400k purchase, primary, SFR"},
          {"role": "assistant", "content": "**Max LTV/CLTV: 97%**"}]),
        ("NonQM", "Ignite 1099", "deal", "how many months of reserves on a cash-out?", "guide", []),
        ("NonQM", "Ignite 1099", "ask", "what's the max LTV for a 700 FICO cash-out primary at $1.2M?", "deal", []),
        ("NonQM", "Ignite 1099", "deal", "700 FICO cash out", "missing", []),
        ("NonQM", "", "ask", "Can a first time homebuyer do a DSCR loan?", "guide", []),
        ("NonQM", "", "ask", "What's the minimum loan amount on Ignite DSCR?", "guide", []),
        ("NonQM", "Ignite Express DSCR", "ask", "Is a DSCR of 0.75 allowed with a 660 FICO?", "guide", []),
        ("FHA", "", "deal", "Can the borrower use gift funds for the down payment?", "guide", []),
        ("VA", "", "deal", "Is a VA cash-out allowed above 90% LTV?", "guide", []),
    ]
    return [dict(id=f"route-{i:02d}", section=s, program=p, mode=m, q=q, history=h, expect=dict(route=r))
            for i, (s, p, m, q, r, h) in enumerate(rows)]


def guide(section, program, items, prefix):
    return [dict(id=f"{prefix}-{i:02d}", section=section, program=program, mode="ask", q=q,
                 expect=dict(route="guide", **e)) for i, (q, e) in enumerate(items)]


def nonqm_guide():
    allq = [
        ("How long after a Chapter 7 bankruptcy discharge can the borrower qualify?", {}),
        ("Foreclosure seasoning requirement?", {}),
        ("Short sale or deed in lieu, how much seasoning?", {}),
        ("Borrower has a 1x30 in the last 12 months on their mortgage, is that ok?", {}),
        ("Are ITIN borrowers eligible?", {}),
        ("Can foreign nationals qualify?", {}),
        ("Is a non-warrantable condo allowed?", {}),
        ("Are condotels eligible?", {}),
        ("Max acreage for rural properties?", {}),
        ("Can I use gift funds for reserves?", {}),
        ("Can cash out proceeds be used for reserves?", {}),
        ("Is interest only available?", {}),
        ("Is there a prepayment penalty on investment properties?", {}),
        ("Max DTI?", {}),
        ("Do non-occupant co-borrowers work on these programs?", {}),
        ("What's the minimum credit score for a first time investor on DSCR?", {}),
        ("Do I need 2 appraisals on a large loan? What's the threshold?", {}),
        ("Can we use 12 months of bank statements instead of 24?", {}),
        ("What expense factor is used on business bank statements?", {}),
        ("Can a 1099 borrower use only 1 year of 1099s?", {}),
        ("How is P&L only income documented? Who has to prepare the P&L?", {}),
        ("Is asset depletion / asset utilization allowed?", {}),
        ("Short-term rental income on DSCR, how is it calculated?", {}),
        ("Is a DSCR under 1.00 allowed?", {}),
        ("Seller concessions max?", {}),
        ("Can the borrower be a first time homebuyer on Ignite Bank Statements?", {}),
        ("What's the max cash out amount?", {}),
        ("Are LLC vestings allowed on DSCR?", {}),
        ("How many financed properties can the borrower have?", {}),
        ("What tradeline requirements are there?", {}),
        ("Housing history required? What if they live rent free?", {}),
        ("What's the reserve requirement for a $2M loan?", {}),
        ("BK7 2 yrs ago, ignite bank statement ok?", {}),
        ("Do we need a CPA letter for bank statement loans?", {}),
        ("Can an NOD in the last 2 years be ok?", {}),
        ("Declining markets, any LTV reduction?", {}),
        ("Are 2-4 unit properties eligible on Ignite 1099 and is there an LTV hit?", {}),
        ("What's the difference between Express and Ignite bank statement programs?", {}),
        ("Can the 2nd mortgage be behind a HELOC?", {}),
        ("Minimum loan amount on the Express 2nd?", {}),
    ]
    one = [
        ("Ignite Express DSCR", "Minimum DSCR and what LTV can I get at 0.80?", {}),
        ("Ignite Express DSCR", "First time investor, any restrictions?", {}),
        ("Ignite Express DSCR", "Are short term rentals allowed and how do I calculate the rents?", {}),
        ("Ignite 1099", "How many months of reserves do I need on a 2-unit primary?", {}),
        ("Ignite 1099", "Can a W-2 wage earner with a 1099 side job use this program?", {}),
        ("Ignite Bank Statements", "Personal vs business bank statements, what's the difference in calculation?", {}),
        ("Ignite Full Doc", "Can we use one year of tax returns?", {}),
        ("Ignite P&L", "Does the P&L need to be CPA prepared?", {}),
        ("Express DSCR", "What's the max LTV for a cash-out with a DSCR of 0.9?", {}),
        ("Express 2nd", "Max CLTV on the 2nd mortgage program?", {}),
        ("Express Full Doc", "Is a 2-year work history required?", {}),
    ]
    return guide("NonQM", "", allq, "nq-guide") + [
        dict(id=f"nq-one-{i:02d}", section="NonQM", program=p, mode="ask", q=q, expect=dict(route="guide", **e))
        for i, (p, q, e) in enumerate(one)]


def conv_guide():
    allq = [
        ("What are the 95.01-97% LTV requirements on Fannie?", dict(must=[r"first[- ]time"])),
        ("Are temporary buydowns allowed on investment properties?", dict(must=[r"(?i)not|ineligible"])),
        ("What's the CLTV limit with a community second on Fannie?", dict(must=[r"105"])),
        ("Does Freddie allow a non-occupant co-borrower on Refi Possible?", dict(must=[r"95"])),
        ("How many months of reserves for a Freddie investment property?", {}),
        ("How long after a Chapter 7 bankruptcy for Fannie? And for Freddie?", dict(must=[r"4 years|48 months|four years", r"2 years|24 months|two years"])),
        ("What's the income limit for HomeReady?", dict(must=[r"80%"])),
        ("Max LTV on Home Possible with a non-occupant co-borrower?", {}),
        ("Reserves needed for a 2-unit primary on Fannie?", dict(must=[r"6 months|six months"])),
        ("What ARM products are offered, and what's the margin?", dict(must=[r"2\.75"])),
        ("Does HomeOne require a first-time homebuyer?", dict(must=[r"(?i)yes|at least one"])),
        ("Is homebuyer education required on HomeReady?", {}),
        ("Gift funds on a second home for Fannie?", {}),
        ("How does Freddie treat student loan payments when the credit report shows $0?", {}),
        ("Minimum FICO on Refi Possible?", {}),
        ("Max DTI on Fannie with DU?", dict(must=[r"50"])),
        ("How do I calculate rental income from a departing residence on Fannie?", {}),
        ("Can a gift be used on an investment property for Freddie?", {}),
        ("What's the minimum loan amount on conventional?", dict(must=[r"75,000"])),
        ("Are ARMs allowed above 95% LTV?", {}),
        ("What property types are allowed on HomeOne?", {}),
        ("Max cash back on Refi Possible?", dict(must=[r"250"])),
        ("Can I do a cash-out on a manufactured home with Freddie?", {}),
        ("Seller contributions limit on a 95% LTV purchase with Fannie?", {}),
        ("Is mortgage insurance required at 85% LTV and how much coverage?", {}),
        ("Waiting period after a short sale on Fannie?", {}),
        ("Does Fannie allow a borrower to have 10 financed properties?", {}),
        ("Self-employed borrower with 1 year of returns on Fannie?", {}),
        ("Max LTV for a 3-unit investment cash-out on Freddie?", dict(must=[r"70"])),
        ("Are high balance loans allowed on HomeOne?", dict(must=[r"(?i)no|conforming"])),
    ]
    return guide("Conventional", "", allq, "conv-guide")


def gov_guide():
    fha = [
        ("FHA minimum credit score and max LTV?", {}),
        ("FHA MIP on a 30 year at 96.5% LTV?", {}),
        ("FHA deferred student loan, how is the payment calculated?", dict(must=[r"0\.5"])),
        ("FHA non-occupant co-borrower, is it allowed?", {}),
        ("Can FHA be used on an investment property?", {}),
        ("FHA waiting period after Chapter 13?", {}),
        ("FHA gift funds, who can give?", {}),
        ("FHA streamline, is an appraisal required?", {}),
        ("FHA manufactured home eligibility?", {}),
        ("FHA cash-out max LTV?", dict(must=[r"80"])),
        ("FHA collections, do they need to be paid?", {}),
        ("FHA minimum loan amount on the NDM sheet?", dict(must=[r"75,000"])),
    ]
    va = [
        ("VA residual income requirements?", {}),
        ("VA funding fee exemption, who qualifies?", {}),
        ("VA IRRRL, is there an LTV limit?", {}),
        ("VA cash-out above 90%, what's required?", {}),
        ("VA minimum credit score on the NDM sheet?", {}),
        ("VA entitlement on a second use, how does it work?", {}),
        ("VA max loan amount with full entitlement?", {}),
        ("Can a VA loan be used for a 4-unit property?", {}),
        ("VA seasoning requirement for an IRRRL?", {}),
        ("VA manufactured home rules?", {}),
        ("VA DTI max?", {}),
        ("VA funding fee for a first use purchase with 0 down?", {}),
    ]
    return guide("FHA", "", fha, "fha-guide") + guide("VA", "", va, "va-guide")


def gov_deals():
    rows = [
        ("FHA", "", "620 FICO, $350k purchase, primary, SFR"),
        ("FHA", "FHA Standard", "560 FICO, $300k purchase, primary"),
        ("FHA", "FHA Standard", "600 FICO, $400k cash-out refi, primary"),
        ("VA", "", "640 FICO, $700k purchase, primary"),
        ("VA", "VA Standard", "700 FICO, $2M purchase, primary"),
        ("VA", "VA Standard", "620 FICO, $900k cash-out, primary"),
    ]
    return [dict(id=f"gov-deal-{i}", section=s, program=p, mode="deal", q=q, expect=dict(route="deal"))
            for i, (s, p, q) in enumerate(rows)]


def bank():
    return conv_deals() + phrasing() + nonqm_deals() + routing() + nonqm_guide() + conv_guide() + gov_guide() + gov_deals()


if __name__ == "__main__":
    b = bank()
    from collections import Counter
    print(len(b), Counter(c["id"].split("-")[0] for c in b))
