from django.apps import AppConfig


class PartnersConfig(AppConfig):
    name = "apps.partners"
    label = "partners"
    verbose_name = "Customers & suppliers"

    def ready(self):
        from auditlog.registry import auditlog

        from .models import Contact, Customer, Supplier

        auditlog.register(Customer)
        auditlog.register(Supplier)
        auditlog.register(Contact)
