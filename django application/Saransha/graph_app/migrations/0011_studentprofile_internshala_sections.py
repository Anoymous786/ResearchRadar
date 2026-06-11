from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("graph_app", "0010_alter_users_publication_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="summary",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="location",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="availability",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="social_links",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="education_entries",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="project_entries",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="experience_entries",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="certification_entries",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="interests_entries",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="languages_entries",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="achievements_entries",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
