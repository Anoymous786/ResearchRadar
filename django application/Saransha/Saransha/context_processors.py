"""
Context processors to make user information available to all templates
"""
from graph_app.models import Users_Publication


def user_context(request):
    """
    Add user information to template context
    Checks if logged-in user is a faculty member
    """
    context = {
        'is_faculty': False,
        'current_user': None,
    }
    
    # Check if user is logged in via session
    if "user_email" in request.session:
        try:
            user = Users_Publication.objects.get(user_email=request.session["user_email"])
            context['current_user'] = user
            
            # Check if user is a faculty member (prefer `role` but keep backward compatibility with `user_category`)
            role = (user.role or user.user_category or "").lower().strip()
            faculty_roles = ['faculty', 'professor', 'associate professor', 'assistant professor']
            context['is_faculty'] = role == 'faculty' or role in faculty_roles
        except Users_Publication.DoesNotExist:
            pass
    
    return context
