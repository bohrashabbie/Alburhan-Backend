from django.core.management.base import BaseCommand

from apps.access.catalog import sync_permissions


class Command(BaseCommand):
    help = (
        "Write every app's permissions_catalog to the database and refresh the Administrator role."
    )

    def handle(self, *args, **options):
        result = sync_permissions()
        self.stdout.write(self.style.SUCCESS(f"Permissions synced: {result}"))
