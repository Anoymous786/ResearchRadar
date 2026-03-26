# Saransha/views.py

from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.core.files.storage import FileSystemStorage
from django.contrib import messages
from django.db import IntegrityError
import pandas as pd
import openpyxl
import os
import io
from datetime import datetime
from graph_app.groq_client import generate_ai_response
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
import json
from graph_app.groq_client import generate_ai_response

from .utils import (
    load_and_filter_excel,  
    get_publications_from_profile,
    get_publications_safe,
    process_profiles_from_excel,
    generate_author_summary,
    update_publication_details
)

from graph_app.models import Users_Publication, FacultyProfile, Publication, StudentProfile
from graph_app.forms import FacultyProfileForm
import os
from graph_app.student_ai import (
    extract_pdf_text,
    extract_resume_fields,
    analyze_resume_with_groq,
    analyze_research_paper_with_groq,
    rule_based_resume_analysis,
    rule_based_research_paper_analysis,
    analyze_resume_rule_based_json,
)


FACULTY_ROLES = {'faculty', 'professor', 'associate professor', 'assistant professor'}


def _get_effective_role(user: Users_Publication) -> str:
    """
    Prefer the new `role` field; fall back to legacy `user_category` for backward compatibility.
    """
    role = (getattr(user, "role", "") or "").lower().strip()
    if role in {"student", "faculty", "organization"}:
        # Treat organization accounts as student for dashboard/routing purposes.
        return "student" if role == "organization" else role

    user_category = (getattr(user, "user_category", "") or "").lower().strip()
    if user_category in FACULTY_ROLES:
        return "faculty"

    # Default to student if it's not clearly faculty.
    return "student"


def _get_logged_in_user(request):
    if "user_email" not in request.session:
        return None
    return Users_Publication.objects.filter(user_email=request.session["user_email"]).first()


# =====================================================
# UPLOAD PAGE (EXCEL OR GOOGLE SCHOLAR URL)
# =====================================================

def upload_page(request):
    if "user_email" not in request.session:
        return redirect("login")

    # Faculty-only area; keep student users out of the Excel pipeline.
    user = _get_logged_in_user(request)
    if user and _get_effective_role(user) != "faculty":
        return redirect("student_dashboard")

    excel_data = []
    publications_data = []
    error_message = None
    success_message = None
    profile_url_value = ""

    if request.method == "POST":
        excel_file = request.FILES.get("excel_file")
        profile_url = request.POST.get("profile_url", "").strip()
        profile_url_value = profile_url

        # Neither provided
        if not excel_file and not profile_url:
            error_message = "Please upload an Excel file OR paste a Google Scholar profile URL."
            return render(request, "auth/upload.html", locals())

        fs = FileSystemStorage()

        # =================================================
        # CASE 1: GOOGLE SCHOLAR URL ONLY
        # =================================================
        if profile_url and not excel_file:
            try:
                print(f"[INFO] Processing single profile URL: {profile_url}")
                publications = get_publications_from_profile(profile_url, timeout=30, max_publications=100)

                if not publications:
                    error_message = "No publications found. Please check the Google Scholar profile URL."
                else:
                    publications_data = publications
                    success_message = f"Fetched {len(publications)} publications from Google Scholar."
                    
                    # Save to file for later use
                    output_file = fs.path("all_authors_publications.xlsx")
                    pd.DataFrame(publications).to_excel(output_file, index=False)
                    print(f"[SUCCESS] Saved to {output_file}")
                    messages.success(request, success_message)
                    return redirect("faculty_profile")
                
                messages.error(request, error_message or "Failed to fetch Google Scholar data.")
                return render(request, "auth/upload.html", locals())

            except Exception as e:
                error_message = f"Error fetching Google Scholar data: {str(e)}"
                print(f"[ERROR] {error_message}")
                return render(request, "auth/upload.html", locals())

        # =================================================
        # CASE 2: EXCEL FILE UPLOAD
        # =================================================
        if excel_file:
            ext = os.path.splitext(excel_file.name)[1].lower()
            if ext not in [".xlsx", ".xls"]:
                error_message = "Invalid file type. Please upload an Excel file (.xlsx or .xls)."
                return render(request, "auth/upload.html", locals())

            filename = fs.save(excel_file.name, excel_file)
            file_path = fs.path(filename)

            # Validate Excel structure
            try:
                wb = openpyxl.load_workbook(file_path)
                ws = wb.active
                headers = [cell.value for cell in ws[1]]

                if "Profile URL" not in headers:
                    error_message = "Excel file must contain a 'Profile URL' column."
                    return render(request, "auth/upload.html", locals())

            except Exception as e:
                error_message = f"Invalid Excel file: {str(e)}"
                return render(request, "auth/upload.html", locals())

            # Process Excel
            try:
                output_file = fs.path("all_authors_publications.xlsx")
                process_profiles_from_excel(file_path, output_file)

                if not os.path.exists(output_file):
                    error_message = "Failed to generate output file."
                    return render(request, "auth/upload.html", locals())

                df = pd.read_excel(output_file)
                if df.empty:
                    error_message = "No publications found. Check Profile URLs."
                else:
                    publications_data = df.to_dict(orient="records")
                    success_message = f"Successfully processed {len(publications_data)} publications."

            except Exception as e:
                error_message = str(e)

    # If we successfully processed uploads, take the faculty back to their profile dashboard.
    if success_message and not error_message:
        messages.success(request, success_message)
        return redirect("faculty_profile")

    return render(request, "auth/upload.html", locals())


# =====================================================
# GENERATE SUMMARY
# =====================================================

def generatesummary(request):
    if "user_email" not in request.session:
        return redirect("login")

    user = _get_logged_in_user(request)
    if user and _get_effective_role(user) != "faculty":
        return redirect("student_dashboard")

    fs = FileSystemStorage()
    output_file_path = fs.path("all_authors_publications.xlsx")

    authors = []
    result_df = pd.DataFrame()
    summary = pd.DataFrame()
    data = ""
    error_message = None

    if not os.path.exists(output_file_path):
        error_message = "Please upload publication data first."
        return render(request, "auth/generatesummary.html", locals())

    try:
        df = pd.read_excel(output_file_path)
        if df.empty or "Main Author" not in df.columns:
            error_message = "Invalid data file. Please upload data again."
            return render(request, "auth/generatesummary.html", locals())

        authors = df["Main Author"].dropna().unique().tolist()
        
        # Show data table by default
        data = df.to_html(classes='table table-striped table-hover', index=False)

    except Exception as e:
        error_message = f"Error reading data file: {str(e)}"
        return render(request, "auth/generatesummary.html", locals())

    if request.method == "POST":
        faculty = request.POST.get("facultySelect", "All")
        start_year = int(request.POST.get("startYear", 0) or 0)
        end_year = int(request.POST.get("endYear", 9999) or 9999)
        sort_by = request.POST.get("sortBy", "")

        try:
            result_df = load_and_filter_excel(
                file_path=output_file_path,
                columns=[
                    "Main Author", "Title", "Journal", "conference",
                    "Publication Type", "Year", "Cited by", "co_author"
                ],
                column_name="Main Author",
                valid_names=authors if faculty == "All" else [faculty],
                year_range=[start_year, end_year],
                cited_by_sort_order=sort_by
            )
            
            if not result_df.empty:
                data = result_df.to_html(classes='table table-striped table-hover', index=False)

        except Exception as e:
            error_message = f"Error filtering data: {str(e)}"

        if "downloadSummary" in request.POST:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                result_df.to_excel(writer, index=False)
            buffer.seek(0)

            response = HttpResponse(
                buffer,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            response["Content-Disposition"] = 'attachment; filename="filtered_summary.xlsx"'
            return response

        if "generateSummary" in request.POST and not result_df.empty:
            try:
                summary = generate_author_summary(result_df)
                data = summary.to_html(classes='table table-striped table-hover', index=False)
            except Exception as e:
                error_message = f"Error generating summary: {str(e)}"

    return render(request, "auth/generatesummary.html", locals())


# =====================================================
# AUTH PAGES
# =====================================================

def home(request):
    if "user_email" not in request.session:
        return render(request, "index.html")

    user = _get_logged_in_user(request)
    if user and _get_effective_role(user) == "student":
        return redirect("student_dashboard")

    # For faculty, route to the existing faculty dashboard UI.
    if user and _get_effective_role(user) == "faculty":
        return redirect("faculty_profile")

    # Fallback: treat any other role as student.
    return redirect("student_dashboard")


def login(request):
    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")

        user = Users_Publication.objects.filter(user_email=email).first()
        if user and str(user.user_password) == str(password):
            # Clear student analyzer-related session so results don't "carry" across profiles.
            for k in [
                "resume_text",
                "resume_fields",
                "resume_analysis_json",
                "resume_analysis_output",
                "paper_text",
                "paper_analysis_output",
            ]:
                request.session.pop(k, None)

            request.session["user_email"] = email

            role = _get_effective_role(user)
            if role == "student":
                return redirect("student_dashboard")
            if role == "faculty":
                return redirect("faculty_profile")
            return redirect("student_dashboard")

        return render(request, "login.html", {"error": "Invalid credentials"})

    return render(request, "login.html")


def signup(request):
    error_message = None
    
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        category = request.POST.get("category", "")
        category_value = (category or "").lower().strip()

        # Map signup selection into the required `role` values.
        role = category_value if category_value in {"student", "faculty", "organization"} else ""
        if role == "":
            # Backward compatibility: map legacy faculty wording to faculty, everything else to student.
            if category_value in FACULTY_ROLES:
                role = "faculty"
            else:
                role = "student"

        user_category = role
        
        # Validate required fields
        if not email or not username or not password or not category:
            error_message = "All fields are required. Please fill in all the information."
            return render(request, "signup.html", {"error": error_message})
        
        # Check if email already exists before attempting to create
        if Users_Publication.objects.filter(user_email=email).exists():
            error_message = "Email already registered. Please use a different email or try logging in."
            return render(request, "    signup.html", {"error": error_message})
        
        # Create the user with error handling
        try:
            new_user = Users_Publication.objects.create(
                user_name=username,
                user_email=email,
                user_password=password,
                user_category=user_category,
                role=role,
            )
            
            # Auto-create profile
            if role == "faculty":
                try:
                    FacultyProfile.objects.create(
                        user=new_user,
                        full_name=username  # Initialize with username
                    )
                except Exception as e:
                    # Log error but don't break signup process
                    print(f"Warning: Could not create FacultyProfile for {new_user.user_email}: {str(e)}")

            if role == "student":
                try:
                    StudentProfile.objects.create(
                        user=new_user,
                    )
                except Exception as e:
                    print(f"Warning: Could not create StudentProfile for {new_user.user_email}: {str(e)}")
            
            # Success - redirect to login
            messages.success(request, "Account created successfully! Please login.")
            return redirect("login")
            
        except IntegrityError as e:
            # Handle database integrity errors (e.g., unique constraint violations)
            error_message = "Email already registered. Please use a different email or try logging in."
            return render(request, "signup.html", {"error": error_message})
            
        except Exception as e:
            # Handle any other unexpected errors
            error_message = "An error occurred while creating your account. Please try again later."
            print(f"Error creating user: {str(e)}")
            return render(request, "signup.html", {"error": error_message})

    return render(request, "signup.html", {"error": error_message})


def logo_view(request):
    request.session.flush()
    return redirect("home")


# =====================================================
# STUDENT MODULE (resume + paper analyzers)
# =====================================================

def student_dashboard(request):
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)

    resume_analysis_json = request.session.get("resume_analysis_json", {})
    paper_analysis_output = request.session.get("paper_analysis_output", "")
    paper_uploaded = bool(request.session.get("paper_text"))

    # Prefill empty manual fields with account values (student can override anytime if not approved).
    if not profile.full_name and user.user_name:
        profile.full_name = user.user_name
    if not profile.email and user.user_email:
        profile.email = user.user_email
    if not profile.phone and getattr(user, "user_phone", None):
        profile.phone = user.user_phone
    if not profile.phone:
        # keep blank if user model doesn't have phone
        pass
    profile.save(update_fields=["full_name", "email"])

    # Handle profile update and approval request.
    if request.method == "POST":
        action = request.POST.get("action", "")

        # Freeze system: once approved, student cannot edit.
        # But allow students to re-request approval (which re-opens editing).
        if profile.is_approved and action != "request_approval":
            messages.warning(request, "Your profile is approved and is now read-only.")
            return redirect("student_dashboard")

        if action == "save_profile":
            full_name = request.POST.get("full_name", "").strip()
            email = request.POST.get("email", "").strip()
            phone = request.POST.get("phone", "").strip()
            cgpa_raw = request.POST.get("cgpa", "").strip()
            skills = request.POST.get("skills", "").strip()
            interests = request.POST.get("interests", "").strip()
            projects = request.POST.get("projects", "").strip()
            experience = request.POST.get("experience", "").strip()

            profile.full_name = full_name
            profile.email = email
            profile.phone = phone
            profile.skills = skills
            profile.interests = interests
            profile.projects = projects
            profile.experience = experience

            if cgpa_raw:
                try:
                    profile.cgpa = cgpa_raw
                except Exception:
                    profile.cgpa = None
            else:
                profile.cgpa = None

            profile.save()
            messages.success(request, "Profile saved. You can request approval when ready.")
            return redirect("student_dashboard")

        if action == "request_approval":
            profile.approval_status = "Pending"
            profile.is_approved = False
            profile.approval_requested = True
            profile.save(update_fields=["approval_status", "is_approved", "approval_requested"])
            messages.success(request, "Approval requested. Faculty will review your profile.")
            return redirect("student_dashboard")

    return render(
        request,
        "student/student_dashboard.html",
        {
            "profile": profile,
            "resume_analysis_json": resume_analysis_json,
            "paper_analysis_output": paper_analysis_output,
            "paper_uploaded": paper_uploaded,
        },
    )


def upload_resume(request):
    """
    Upload student resume PDF (and optionally a research paper PDF),
    parse the PDF text locally, and store extracted fields in session.
    """
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)

    extracted_fields = request.session.get("resume_fields", {})
    error_message = None
    success_message = None

    if request.method == "POST":
        resume_file = request.FILES.get("resume_file")
        paper_file = request.FILES.get("paper_file")

        if not resume_file:
            error_message = "Please upload your resume PDF."
            return render(
                request,
                "student/upload_resume.html",
                {
                    "profile": profile,
                    "extracted_fields": extracted_fields,
                    "success_message": success_message,
                    "error_message": error_message,
                    "resume_analysis_json": request.session.get("resume_analysis_json", {}),
                    "paper_analysis_output": request.session.get("paper_analysis_output", ""),
                },
            )

        if not resume_file.name.lower().endswith(".pdf"):
            error_message = "Resume must be a PDF file."
            return render(
                request,
                "student/upload_resume.html",
                {
                    "profile": profile,
                    "extracted_fields": extracted_fields,
                    "success_message": success_message,
                    "error_message": error_message,
                    "resume_analysis_json": request.session.get("resume_analysis_json", {}),
                    "paper_analysis_output": request.session.get("paper_analysis_output", ""),
                },
            )

        # Extract resume text first, then save file (so we can parse without reloading).
        try:
            resume_text = extract_pdf_text(resume_file)
            resume_fields = extract_resume_fields(resume_text)
        except Exception as e:
            error_message = f"Failed to extract resume text: {str(e)}"
            return render(
                request,
                "student/upload_resume.html",
                {
                    "profile": profile,
                    "extracted_fields": extracted_fields,
                    "success_message": success_message,
                    "error_message": error_message,
                    "resume_analysis_json": request.session.get("resume_analysis_json", {}),
                    "paper_analysis_output": request.session.get("paper_analysis_output", ""),
                },
            )

        # Save resume file to the StudentProfile.
        try:
            resume_file.seek(0)
            profile.resume.save(resume_file.name, resume_file, save=True)
        except Exception as e:
            error_message = f"Failed to save resume: {str(e)}"
            return render(
                request,
                "student/upload_resume.html",
                {
                    "profile": profile,
                    "extracted_fields": extracted_fields,
                    "success_message": success_message,
                    "error_message": error_message,
                    "resume_analysis_json": request.session.get("resume_analysis_json", {}),
                    "paper_analysis_output": request.session.get("paper_analysis_output", ""),
                },
            )

        # Optional research paper upload (bonus).
        paper_text = None
        if paper_file and paper_file.name.lower().endswith(".pdf"):
            try:
                paper_text = extract_pdf_text(paper_file)
            except Exception:
                paper_text = None

        request.session["resume_text"] = resume_text
        request.session["resume_fields"] = resume_fields
        request.session["paper_text"] = paper_text or ""
        request.session["resume_analysis_output"] = ""
        request.session["paper_analysis_output"] = ""
        request.session.modified = True

        # Generate an immediate analysis so the UI never shows only "extracted details".
        # This uses rule-based logic (fast + reliable). Users can still regenerate on /student/analyze/.
        try:
            default_target_role = "General Software/Tech Role"
            request.session["resume_analysis_json"] = analyze_resume_rule_based_json(
                resume_text,
                target_role=default_target_role,
            )
            if paper_text:
                request.session["paper_analysis_output"] = rule_based_research_paper_analysis(paper_text)
            request.session.modified = True
        except Exception as e:
            # Never block upload if analysis generation fails; but surface why it failed.
            error_message = f"Resume uploaded, but resume analyzer failed: {str(e)}"
            request.session["resume_analysis_json"] = {}
            request.session.modified = True

        # Only show a success toast if we also produced analyzer output.
        success_message = "Resume uploaded and parsed successfully." if not error_message else None
        extracted_fields = resume_fields

        if success_message:
            messages.success(request, success_message)
            # After upload, go to Home/Profile so the student sees updated profile + analyzer.
            return redirect("student_dashboard")

    return render(
        request,
        "student/upload_resume.html",
        {
            "profile": profile,
            "extracted_fields": extracted_fields,
            "error_message": error_message,
            "success_message": success_message,
            "resume_analysis_json": request.session.get("resume_analysis_json", {}),
            "paper_analysis_output": request.session.get("paper_analysis_output", ""),
        },
    )


def generate_resume_analysis(request):
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)

    if not profile.resume:
        return render(
            request,
            "student/analyze_resume.html",
            {
                "profile": profile,
                "error_message": "Please upload your resume first.",
                "resume_fields": request.session.get("resume_fields", {}),
                "resume_analysis_json": {},
                "paper_analysis_output": "",
            },
        )

    resume_fields = request.session.get("resume_fields", {})
    resume_text = request.session.get("resume_text", "")
    paper_text = request.session.get("paper_text", "")

    error_message = None
    resume_analysis_json = request.session.get("resume_analysis_json", {})
    paper_analysis_output = request.session.get("paper_analysis_output", "")
    typed_target_role = request.POST.get("target_role", "").strip() if request.method == "POST" else ""

    # If resume_text isn't in session (e.g., refreshed tab), extract again from stored PDF.
    if not resume_text:
        try:
            with profile.resume.open("rb") as f:
                resume_text = extract_pdf_text(f)
            request.session["resume_text"] = resume_text
            if not resume_fields:
                request.session["resume_fields"] = extract_resume_fields(resume_text)
            request.session.modified = True
        except Exception as e:
            error_message = f"Failed to extract resume PDF text: {str(e)}"

    # If analysis JSON is missing (e.g., session cleared), regenerate rule-based analysis on GET.
    if not error_message and not resume_analysis_json and resume_text:
        try:
            resume_analysis_json = analyze_resume_rule_based_json(resume_text, target_role=typed_target_role or "Software Developer")
            request.session["resume_analysis_json"] = resume_analysis_json
            request.session.modified = True
        except Exception as e:
            error_message = f"Error generating resume analysis: {str(e)}"

    if request.method == "POST" and not error_message:
        try:
            resume_analysis_json = analyze_resume_rule_based_json(
                resume_text,
                target_role=typed_target_role or "Software Developer",
            )
        except Exception as e:
            error_message = f"Error generating resume analysis: {str(e)}"

        if paper_text:
            # Rule-based only (NO API).
            try:
                paper_analysis_output = rule_based_research_paper_analysis(paper_text)
            except Exception as e:
                paper_analysis_output = f"Error generating research paper analysis: {str(e)}"
        else:
            paper_analysis_output = ""

        request.session["resume_analysis_json"] = resume_analysis_json
        request.session["paper_analysis_output"] = paper_analysis_output
        request.session.modified = True

    return render(
        request,
        "student/analyze_resume.html",
        {
            "profile": profile,
            "error_message": error_message,
            "resume_fields": resume_fields,
            "resume_analysis_json": resume_analysis_json,
            "paper_analysis_output": paper_analysis_output,
        },
    )


def research_paper_analysis(request):
    """
    Bonus-only endpoint: analyze the uploaded research paper (if present in session).
    """
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)
    resume_fields = request.session.get("resume_fields", {})
    paper_text = request.session.get("paper_text", "") or ""

    error_message = None
    paper_analysis_output = request.session.get("paper_analysis_output", "")

    if request.method == "POST":
        if not paper_text.strip():
            error_message = "Please upload a research paper PDF first (optional on the resume upload page)."
        else:
            try:
                # Rule-based only (NO API).
                paper_analysis_output = rule_based_research_paper_analysis(paper_text)
                request.session["paper_analysis_output"] = paper_analysis_output
                request.session.modified = True
            except Exception as e:
                error_message = f"Error generating research paper analysis: {str(e)}"

    return render(
        request,
        "student/analyze_resume.html",
        {
            "profile": profile,
            "error_message": error_message,
            "resume_fields": resume_fields,
            "resume_analysis_json": request.session.get("resume_analysis_json", {}),
            "paper_analysis_output": paper_analysis_output,
        },
    )


def faculty_student_approvals(request):
    """
    Faculty page: view submitted student profiles and approve/reject them.
    """
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "faculty":
        return redirect("home")

    if request.method == "POST":
        profile_id = request.POST.get("profile_id", "")
        decision = request.POST.get("decision", "")

        try:
            student_profile = StudentProfile.objects.get(id=int(profile_id))
        except Exception:
            messages.error(request, "Student profile not found.")
            return redirect("faculty_student_approvals")

        if decision == "approve":
            student_profile.approval_status = "Approved"
            student_profile.is_approved = True
            student_profile.approval_requested = True
            student_profile.save(update_fields=["approval_status", "is_approved", "approval_requested"])
            messages.success(request, "Student profile approved.")
        elif decision == "reject":
            student_profile.approval_status = "Rejected"
            student_profile.is_approved = False
            student_profile.approval_requested = True
            student_profile.save(update_fields=["approval_status", "is_approved", "approval_requested"])
            messages.warning(request, "Student profile rejected. Student can edit and request again.")
        else:
            messages.error(request, "Invalid action.")

        return redirect("faculty_student_approvals")

    submitted_profiles = StudentProfile.objects.filter(approval_requested=True).order_by("-created_at")

    return render(
        request,
        "faculty/student_approvals.html",
        {
            "submitted_profiles": submitted_profiles,
        },
    )


# =====================================================
# STATIC PAGES
# =====================================================

def settings(request):
    return render(request, 'settings.html')


def help(request):
    return render(request, 'help.html')


def payment(request):
    if "user_email" not in request.session:
        return redirect("login")
    return render(request, 'payment.html')


# =====================================================
# CUST VIEW - Add New Records
# =====================================================

def cust_view(request):
    if "user_email" not in request.session:
        return redirect("login")
    
    success_message = None
    error_message = None
    
    if request.method == "POST":
        try:
            fs = FileSystemStorage()
            output_file = fs.path("all_authors_publications.xlsx")
            
            # Get form data
            main_author = request.POST.get("main_author", "").strip()
            title = request.POST.get("title", "").strip()
            journal = request.POST.get("journal", "").strip() or "N/A"
            conference = request.POST.get("conference", "").strip() or "N/A"
            year = request.POST.get("year", "")
            cited_by = request.POST.get("cited_by", "0")
            
            if not main_author or not title:
                error_message = "Author name and title are required."
            else:
                # Create new record
                new_record = {
                    'Main Author': main_author,
                    'Title': title,
                    'Journal': journal,
                    'conference': conference,
                    'Year': int(year) if year else None,
                    'Publication Type': 'article',
                    'Cited by': int(cited_by) if cited_by else 0,
                    'co_author': main_author,
                    'Last Search Date': datetime.now().strftime("%Y-%m-%d")
                }
                
                # Load existing or create new
                if os.path.exists(output_file):
                    df = pd.read_excel(output_file)
                    df = pd.concat([df, pd.DataFrame([new_record])], ignore_index=True)
                else:
                    df = pd.DataFrame([new_record])
                
                df.to_excel(output_file, index=False)
                success_message = f"Successfully added publication: {title}"
                
        except Exception as e:
            error_message = f"Error adding record: {str(e)}"
    
    return render(request, 'cust.html', {
        'success_message': success_message,
        'error_message': error_message
    })


# =====================================================
# MISSVAL VIEW - Edit Missing Values
# =====================================================

def missVal_view(request):
    if "user_email" not in request.session:
        return redirect("login")
    
    fs = FileSystemStorage()
    output_file = fs.path("all_authors_publications.xlsx")
    
    authors = []
    Title = []
    selected_author = request.GET.get('author', 'All')
    selected_title = request.GET.get('title', None)
    prefill_data = {'journal_name': '', 'conference_name': '', 'year': ''}
    success_message = None
    error_message = None
    
    # Load data if exists
    if os.path.exists(output_file):
        try:
            df = pd.read_excel(output_file)
            authors = df['Main Author'].dropna().unique().tolist()
            
            # Filter titles by selected author
            if selected_author and selected_author != 'All':
                filtered_df = df[df['Main Author'] == selected_author]
                Title = filtered_df['Title'].dropna().unique().tolist()
            else:
                Title = df['Title'].dropna().unique().tolist()
            
            # Prefill data for selected title
            if selected_title and selected_title != 'None':
                title_row = df[df['Title'] == selected_title]
                if not title_row.empty:
                    row = title_row.iloc[0]
                    prefill_data = {
                        'journal_name': row.get('Journal', '') if row.get('Journal') != 'N/A' else '',
                        'conference_name': row.get('conference', '') if row.get('conference') != 'N/A' else '',
                        'year': str(int(row.get('Year'))) if pd.notna(row.get('Year')) else ''
                    }
                    
        except Exception as e:
            error_message = f"Error loading data: {str(e)}"
    
    # Handle form submission
    if request.method == "POST":
        try:
            journal_name = request.POST.get('journalName', '').strip()
            conference_name = request.POST.get('conferenceName', '').strip()
            year = request.POST.get('year', '').strip()
            
            if selected_title and selected_title != 'None' and os.path.exists(output_file):
                df = pd.read_excel(output_file)
                
                # Update the record
                mask = df['Title'] == selected_title
                if journal_name:
                    df.loc[mask, 'Journal'] = journal_name
                if conference_name:
                    df.loc[mask, 'conference'] = conference_name
                if year:
                    df.loc[mask, 'Year'] = int(year)
                
                df.to_excel(output_file, index=False)
                success_message = f"Successfully updated: {selected_title}"
            else:
                error_message = "Please select a title to update."
                
        except Exception as e:
            error_message = f"Error updating record: {str(e)}"
    
    return render(request, 'missVal.html', {
        'authors': authors,
        'Title': Title,
        'selected_author': selected_author,
        'selected_title': selected_title,
        'prefill_data': prefill_data,
        'success_message': success_message,
        'error_message': error_message
    })


# =====================================================
# UPLOAD REDIRECT
# =====================================================

def upload_redirect(request):
    if "user_email" not in request.session:
        return redirect("login")
    return redirect("upload")


# =====================================================
# FACULTY PROFILE MANAGEMENT
# =====================================================

def faculty_profile(request):
    """Dashboard view for faculty to manage their profile"""
    # Check if user is logged in
    if "user_email" not in request.session:
        return redirect("login")
    
    # Get the logged-in user
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    # Check if user is a faculty member (supports both legacy `user_category` and new `role`)
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    # Get or create faculty profile
    try:
        profile = FacultyProfile.objects.get(user=user)
    except FacultyProfile.DoesNotExist:
        # Create a new profile if it doesn't exist
        profile = FacultyProfile.objects.create(
            user=user,
            full_name=user.user_name  # Initialize with username
        )
        messages.info(request, "Profile created. Please complete your profile information.")
    
    # Get publications for this faculty
    publications = profile.publications.all()
    
    # Calculate metrics
    total_publications = profile.get_total_publications()
    total_citations = profile.get_total_citations()
    h_index = profile.get_h_index()
    i10_index = profile.get_i10_index()
    
    context = {
        'profile': profile,
        'user': user,
        'publications': publications,
        'total_publications': total_publications,
        'total_citations': total_citations,
        'h_index': h_index,
        'i10_index': i10_index,
    }
    
    return render(request, 'faculty/profile.html', context)


def faculty_profile_edit(request):
    """View for editing faculty profile information"""
    if "user_email" not in request.session:
        return redirect("login")
    
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    try:
        profile = FacultyProfile.objects.get(user=user)
    except FacultyProfile.DoesNotExist:
        messages.error(request, "Profile not found.")
        return redirect("faculty_profile")
    
    if request.method == "POST":
        form = FacultyProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Profile updated successfully!")
                return redirect("faculty_profile")
            except Exception as e:
                messages.error(request, f"Error saving profile: {str(e)}")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = FacultyProfileForm(instance=profile)
    
    return render(request, 'faculty/profile_edit.html', {
        'form': form,
        'profile': profile,
        'user': user
    })


def faculty_photo_change(request):
    """View for changing profile photo"""
    if "user_email" not in request.session:
        return redirect("login")
    
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    try:
        profile = FacultyProfile.objects.get(user=user)
    except FacultyProfile.DoesNotExist:
        messages.error(request, "Profile not found.")
        return redirect("faculty_profile")
    
    if request.method == "POST" and 'profile_picture' in request.FILES:
        try:
            # Delete old photo if exists
            if profile.profile_picture:
                old_photo_path = profile.profile_picture.path
                if os.path.exists(old_photo_path):
                    os.remove(old_photo_path)
            
            # Save new photo
            profile.profile_picture = request.FILES['profile_picture']
            profile.save()
            messages.success(request, "Profile photo updated successfully!")
        except Exception as e:
            messages.error(request, f"Error updating photo: {str(e)}")
    
    return redirect("faculty_profile")


def faculty_photo_remove(request):
    """View for removing profile photo"""
    if "user_email" not in request.session:
        return redirect("login")
    
    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect("faculty_profile")
    
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    try:
        profile = FacultyProfile.objects.get(user=user)
    except FacultyProfile.DoesNotExist:
        messages.error(request, "Profile not found.")
        return redirect("faculty_profile")
    
    try:
        # Delete photo file if exists
        if profile.profile_picture:
            photo_path = profile.profile_picture.path
            if os.path.exists(photo_path):
                os.remove(photo_path)
            profile.profile_picture = None
            profile.save()
            messages.success(request, "Profile photo removed successfully!")
        else:
            messages.info(request, "No photo to remove.")
    except Exception as e:
        messages.error(request, f"Error removing photo: {str(e)}")
    
    return redirect("faculty_profile")


def faculty_publication_add(request):
    """View for adding a new publication"""
    if "user_email" not in request.session:
        return redirect("login")
    
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    try:
        profile = FacultyProfile.objects.get(user=user)
    except FacultyProfile.DoesNotExist:
        messages.error(request, "Profile not found.")
        return redirect("faculty_profile")
    
    if request.method == "POST":
        try:
            title = request.POST.get("title", "").strip()
            year = request.POST.get("year", "")
            journal = request.POST.get("journal", "").strip()
            citations = request.POST.get("cited_by", "0")
            
            if not title:
                messages.error(request, "Title is required.")
                return redirect("faculty_profile")
            
            Publication.objects.create(
                main_author=profile.full_name or user.user_name,
                title=title,
                year=int(year) if year else datetime.now().year,
                cited_by=int(citations) if citations else 0,
                conference_journal=journal,
                faculty=profile
            )
            messages.success(request, "Publication added successfully!")
        except Exception as e:
            messages.error(request, f"Error adding publication: {str(e)}")
    
    return redirect("faculty_profile")


def faculty_publication_edit(request, pub_id):
    """View for editing a publication"""
    if "user_email" not in request.session:
        return redirect("login")
    
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    try:
        profile = FacultyProfile.objects.get(user=user)
        publication = Publication.objects.get(id=pub_id, faculty=profile)
    except (FacultyProfile.DoesNotExist, Publication.DoesNotExist):
        messages.error(request, "Publication not found.")
        return redirect("faculty_profile")
    
    if request.method == "POST":
        try:
            publication.title = request.POST.get("title", "").strip()
            publication.year = int(request.POST.get("year", datetime.now().year))
            publication.conference_journal = request.POST.get("journal", "").strip()
            publication.cited_by = int(request.POST.get("cited_by", "0") or "0")
            publication.save()
            messages.success(request, "Publication updated successfully!")
            return redirect("faculty_profile")
        except Exception as e:
            messages.error(request, f"Error updating publication: {str(e)}")
    
    return render(request, 'faculty/publication_edit.html', {
        'publication': publication,
        'profile': profile
    })

@csrf_exempt
def chatbot(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            user_input = data.get("message", "")

            if not user_input:
                return JsonResponse({"response": "Please enter a message."})

            ai_response = generate_ai_response(user_input)

            return JsonResponse({"response": ai_response})

        except Exception as e:
            return JsonResponse({"response": f"Error: {str(e)}"})

    return JsonResponse({"response": "Invalid request"})


# ---------------- DELETE PUBLICATION VIEW ----------------
def faculty_publication_delete(request, pub_id):
    """View for deleting a publication"""

    if "user_email" not in request.session:
        return redirect("login")

    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect("faculty_profile")

    try:
        user = Users_Publication.objects.get(
            user_email=request.session["user_email"]
        )
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")

    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")

    try:
        profile = FacultyProfile.objects.get(user=user)
        publication = Publication.objects.get(id=pub_id, faculty=profile)
        publication.delete()
        messages.success(request, "Publication deleted successfully!")

    except (FacultyProfile.DoesNotExist, Publication.DoesNotExist):
        messages.error(request, "Publication not found.")

    except Exception as e:
        messages.error(request, f"Error deleting publication: {str(e)}")

    return redirect("faculty_profile")