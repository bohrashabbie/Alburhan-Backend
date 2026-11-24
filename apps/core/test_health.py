from django.test import Client, override_settings


@override_settings(ALLOWED_HOSTS=["erp.example.com", "api"], SECURE_SSL_REDIRECT=True)
def test_health_answers_the_container_check_on_127_0_0_1():
    client = Client()
    response = client.get("/health/", HTTP_HOST="127.0.0.1:8000")
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    # Everything else still checks the host name.
    assert client.get("/api/v1/me/", HTTP_HOST="127.0.0.1:8000").status_code == 400
