# Generated by Django 5.1.6 on 2025-05-23 16:26

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financialsummary', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='financialsummary',
            name='user',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='financial_summaries', to=settings.AUTH_USER_MODEL),
            preserve_default=False,
        ),
    ]
