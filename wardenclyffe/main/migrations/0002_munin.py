# encoding: utf-8
# flake8: noqa
import datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models

class Migration(SchemaMigration):
    depends_on = (
        ("munin", "0001_initial"),
    )

    def forwards(self):
        pass

    def backwards(self):
        pass