# -*- coding: utf-8 -*-
# Generated by Django 1.11.28 on 2021-06-25 23:13
from __future__ import unicode_literals

from django.db import migrations
import morango.models.fields.uuids


class Migration(migrations.Migration):

    dependencies = [
        ('morango', '0016_store_deserialization_error'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='last_transfer_session_id',
            field=morango.models.fields.uuids.UUIDField(blank=True, db_index=True, default=None, null=True),
        ),
    ]
