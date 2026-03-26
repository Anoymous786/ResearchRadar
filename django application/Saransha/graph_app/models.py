

# Create your models here.
from django.db import models

# Create your models here.
class Users_Publication(models.Model):
    user_name = models.CharField(max_length=100, default='')
    user_email = models.CharField(max_length=100, unique=True,default='')
    user_password = models.CharField(max_length=100, default='')
    user_category = models.CharField(max_length=50, default='')
    role = models.CharField(
        max_length=20,
        blank=True,
        default='',
        choices=[('student', 'student'), ('faculty', 'faculty'), ('organization', 'organization')],
    )

    


class Publication(models.Model):
    main_author = models.CharField(max_length=255)
    title = models.CharField(max_length=500)
    year = models.IntegerField()
    cited_by = models.IntegerField(default=0)
    co_author = models.TextField(blank=True, default='')
    conference_journal = models.CharField(max_length=255, null=True, blank=True)
    domains = models.TextField(blank=True, default='')
    download_links = models.TextField(blank=True, default='')
    # Link to faculty profile
    faculty = models.ForeignKey('FacultyProfile', on_delete=models.CASCADE, related_name='publications', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    
    def __str__(self):
        return f"{self.title[:50]}... ({self.year})"
    
    class Meta:
        ordering = ['-year', '-cited_by']


class FacultyProfile(models.Model):
    """Extended profile for faculty members"""
    user = models.OneToOneField(Users_Publication, on_delete=models.CASCADE, related_name='faculty_profile')
    full_name = models.CharField(max_length=200, blank=True, default='')
    designation = models.CharField(max_length=100, blank=True, default='')  # Professor, Associate Professor, etc.
    department = models.CharField(max_length=100, blank=True, default='')
    bio = models.TextField(blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    office_location = models.CharField(max_length=200, blank=True, default='')
    research_interests = models.TextField(blank=True, default='')
    google_scholar_id = models.CharField(max_length=200, blank=True, default='')
    orcid_id = models.CharField(max_length=50, blank=True, default='')
    profile_picture = models.ImageField(upload_to='profiles/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.full_name or self.user.user_name} - {self.department or 'No Department'}"
    
    def get_total_publications(self):
        """Get total number of publications"""
        return self.publications.count()
    
    def get_total_citations(self):
        """Get total citations"""
        from django.db.models import Sum
        return self.publications.aggregate(total=Sum('cited_by'))['total'] or 0
    
    def get_h_index(self):
        """Calculate h-index"""
        citations = list(self.publications.values_list('cited_by', flat=True).order_by('-cited_by'))
        h = 0
        for i, citations_count in enumerate(citations, 1):
            if citations_count >= i:
                h = i
            else:
                break
        return h
    
    def get_i10_index(self):
        """Calculate i10-index (number of publications with at least 10 citations)"""
        return self.publications.filter(cited_by__gte=10).count()
    
    def get_research_tags(self):
        """Get research interests as a list"""
        if self.research_interests:
            return [tag.strip() for tag in self.research_interests.split(',') if tag.strip()]
        return []
    
    class Meta:
        verbose_name = "Faculty Profile"
        verbose_name_plural = "Faculty Profiles"


class StudentProfile(models.Model):
    """
    Extended student profile for resume-based analysis.
    """

    user = models.OneToOneField(
        Users_Publication,
        on_delete=models.CASCADE,
        related_name="student_profile",
    )

    # Required by spec
    profile_pic = models.ImageField(upload_to="student_profiles/", blank=True, null=True)
    cgpa = models.DecimalField(max_digits=4, decimal_places=2, blank=True, null=True)
    skills = models.TextField(blank=True, default="")
    interests = models.TextField(blank=True, default="")
    projects = models.TextField(blank=True, default="")
    experience = models.TextField(blank=True, default="")
    resume = models.FileField(upload_to="resumes/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Extra fields already present in older migrations in this repo
    full_name = models.CharField(max_length=200, blank=True, default="")
    email = models.CharField(max_length=150, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    branch = models.CharField(max_length=200, blank=True, default="")
    college = models.CharField(max_length=200, blank=True, default="")
    degree = models.CharField(max_length=200, blank=True, default="")
    target_role = models.CharField(max_length=200, blank=True, default="")
    year = models.IntegerField(blank=True, null=True)

    # Approval / freeze system
    approval_status = models.CharField(
        max_length=20,
        choices=[
            ("Pending", "Pending"),
            ("Approved", "Approved"),
            ("Rejected", "Rejected"),
        ],
        default="Pending",
    )
    is_approved = models.BooleanField(default=False)
    approval_requested = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.full_name or self.user.user_name} - Student Profile"

    class Meta:
        verbose_name = "Student Profile"
        verbose_name_plural = "Student Profiles"