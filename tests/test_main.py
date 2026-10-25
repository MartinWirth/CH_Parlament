from app.main import normalize, is_current

def test_normalize():
    result=normalize({"PersonNumber":12,"FirstName":"Anna","LastName":"Muster","CouncilName":"Nationalrat","PartyAbbreviation":"SP"})
    assert result["name"]=="Anna Muster"
    assert result["partei"]=="SP"

def test_inactive():
    assert not is_current({"Active":False})
    assert is_current({"Active":True})
    assert not is_current({"DateLeaving":"2020-01-01"})
