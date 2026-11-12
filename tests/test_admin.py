from guidebot import admin


def test_admin_only_through_cloudflare():
    assert admin.allowed({"Cf-Connecting-Ip": "1.2.3.4", "Cf-Access-Authenticated-User-Email": "Derrick.Garcia.R@gmail.com"})
    assert not admin.allowed({"Cf-Connecting-Ip": "1.2.3.4", "Cf-Access-Authenticated-User-Email": "nwilson@revofunding.com"})
    assert not admin.allowed({"Cf-Ray": "abc"})   # through the tunnel with no signed-in user


def test_direct_from_this_pc_is_allowed():
    assert admin.allowed({})


def test_unknown_document_is_refused():
    assert "error" in admin.approve_document("No Such Program", "x.pdf", False, [])
    assert "error" in admin.approve_rules("No Such Program", "x.pdf")
