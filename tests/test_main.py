from app.main import normalize, is_current

def test_normalize():
    result=normalize({"PersonNumber":12,"FirstName":"Anna","LastName":"Muster","CouncilName":"Nationalrat","PartyAbbreviation":"SP"})
    assert result["name"]=="Anna Muster"
    assert result["partei"]=="SP"

def test_inactive():
    assert not is_current({"Active":False})
    assert is_current({"Active":True})
    assert not is_current({"DateLeaving":"2020-01-01"})


def test_social_media():
    from app.main import social_media
    assert social_media({"TwitterUrl": "https://x.com/example", "FacebookUrl": "javascript:alert(1)"}) == {"X": "https://x.com/example"}
    assert normalize({"FirstName": "Anna"})["socialmedia"] == {}
