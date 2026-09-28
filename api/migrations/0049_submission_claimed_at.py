from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0048_auto_20250923_0316"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
