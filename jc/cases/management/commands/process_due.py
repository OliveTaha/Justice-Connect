from django.core.management.base import BaseCommand

from cases.services import process_due


class Command(BaseCommand):
    help = "Run time based jobs now (automatic withdrawals, deadline reminders)."

    def handle(self, *args, **options):
        self.stdout.write(f"Processed {process_due()} item(s).")
