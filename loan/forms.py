from django  import forms

from .models import *


class LoanRepaybackForm(forms.ModelForm):
    class meta:
        model = LoanRepayback

        fields = "__all__"