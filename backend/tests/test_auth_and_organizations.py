def register(client, email="owner@example.org", organization="Northwind"):
    return client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "CarbonTracePass123",
            "display_name": "Test Owner",
            "organization_name": organization,
            "country_code": "IN",
            "timezone": "Asia/Kolkata",
        },
    )


def test_registration_login_and_me(client):
    registration = register(client)
    assert registration.status_code == 201
    body = registration.json()
    assert body["organization"]["name"] == "Northwind"
    assert body["token_type"] == "bearer"

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    profile = client.get("/api/v1/auth/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["user"]["email"] == "owner@example.org"
    assert profile.json()["organizations"][0]["role"] == "owner"

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "OWNER@example.org", "password": "CarbonTracePass123"},
    )
    assert login.status_code == 200


def test_duplicate_registration_and_invalid_password_are_rejected(client):
    assert register(client).status_code == 201
    assert register(client).status_code == 409
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "weak@example.org",
            "password": "short",
            "display_name": "Weak",
            "organization_name": "Weak Org",
        },
    )
    assert response.status_code == 422


def test_organization_access_is_membership_scoped(client):
    first = register(client, email="first@example.org", organization="First")
    second = register(client, email="second@example.org", organization="Second")
    first_org_id = first.json()["organization"]["id"]
    second_token = second.json()["access_token"]

    own_response = client.get(
        f"/api/v1/organizations/{second.json()['organization']['id']}",
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert own_response.status_code == 200

    cross_tenant_response = client.get(
        f"/api/v1/organizations/{first_org_id}",
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert cross_tenant_response.status_code == 404


def test_organization_update_requires_admin_role(client):
    registration = register(client)
    organization_id = registration.json()["organization"]["id"]
    token = registration.json()["access_token"]
    response = client.put(
        f"/api/v1/organizations/{organization_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Northwind Updated"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Northwind Updated"
