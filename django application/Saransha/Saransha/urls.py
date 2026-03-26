from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('upload/', views.upload_page, name='upload'),
    path('generate-summary/', views.generatesummary, name='generatesummary'),
    path('settings/', views.settings, name='settings'),
    path('login/', views.login, name='login'),
    path('help/', views.help, name='help'),
    path('admin/', admin.site.urls),
    path('graph/', include('graph_app.urls')),
    path('signup/', views.signup, name="signup"),
    path('logo/', views.logo_view, name='logout'),
    path('cust/', views.cust_view, name='cust'),
    path('missVal/', views.missVal_view, name='missVal'),
    path('upload-redirect/', views.upload_redirect, name='upload_redirect'),
    path('payment/', views.payment, name='payment'),
    path('faculty/profile/', views.faculty_profile, name='faculty_profile'),
    path('faculty/profile/edit/', views.faculty_profile_edit, name='faculty_profile_edit'),
    path('faculty/photo/change/', views.faculty_photo_change, name='faculty_photo_change'),
    path('faculty/photo/remove/', views.faculty_photo_remove, name='faculty_photo_remove'),
    path('faculty/publication/add/', views.faculty_publication_add, name='faculty_publication_add'),
    path('faculty/publication/edit/<int:pub_id>/', views.faculty_publication_edit, name='faculty_publication_edit'),
    path('faculty/publication/delete/<int:pub_id>/', views.faculty_publication_delete, name='faculty_publication_delete'),
    path('faculty/student-approvals/', views.faculty_student_approvals, name='faculty_student_approvals'),
    path('chatbot/', views.chatbot, name='chatbot'),

    # =====================================================
    # Student module (resume + paper analyzers)
    # =====================================================
    path('student/dashboard/', views.student_dashboard, name='student_dashboard'),
    path('student/upload/', views.upload_resume, name='student_upload'),
    path('student/analyze/', views.generate_resume_analysis, name='student_analyze'),
    path('student/research/', views.research_paper_analysis, name='research_paper_analysis'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)