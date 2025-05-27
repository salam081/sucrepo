from django.shortcuts import render, redirect,get_object_or_404
from django.contrib.auth.decorators import login_required,user_passes_test
from datetime import date
from django.db.models import Sum
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponse
from xhtml2pdf import pisa
from django.template.loader import get_template
from io import BytesIO
from django.template.loader import render_to_string
from django.db.models import Q, Prefetch
from django.core.paginator import Paginator
from django.db.models.functions import ExtractYear
from decimal import Decimal
from .models import *
from accounts.models import *
from main.models import *
from memberapp.models import *
from consumable.models import *
from tablib import Dataset
import os
import traceback
from .resources import LoanRepaybackResources


from .models import LoanRepayback, LoanRequest, Member
from decimal import Decimal
from datetime import datetime
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db import transaction
import pandas as pd
import os
from django.conf import settings
from django.utils.dateparse import parse_date


def add_single_loan_payment(request):
    if request.method == "POST":
        ippis = request.POST.get("ippis")
        amount_paid = request.POST.get("amount_paid")
        month = request.POST.get("month")  # Expected format: YYYY-MM-DD

        # Check for missing fields
        if not ippis or not amount_paid or not month:
            messages.error(request, "All fields are required.")
            return redirect(request.path)

        # Validate input
        try:
            ippis = int(ippis)
            amount_paid = Decimal(amount_paid)
            if amount_paid <= 0:
                raise ValueError("Amount must be greater than 0.")
            month = parse_date(month)
            if not month:
                raise ValueError("Invalid date format.")
        except Exception as e:
            messages.error(request, f"Invalid input: {e}")
            return redirect(request.path)

        # Get member
        member = Member.objects.filter(ippis=ippis).first()
        if not member:
            messages.error(request, f"No member found with IPPIS: {ippis}")
            return redirect(request.path)

        # Get approved loan request
        loan_request = LoanRequest.objects.filter(member=member, status='approved').first()
        if not loan_request:
            messages.error(request, "No approved loan request found for this member.")
            return redirect(request.path)

        # Check if payment already made for this month
        already_paid = LoanRepayback.objects.filter(
            loan_request=loan_request,
            repayment_date__year=month.year,
            repayment_date__month=month.month
        ).exists()

        if already_paid:
            messages.warning(request, f"A repayment already exists for {month.strftime('%B %Y')}.")
            return redirect(request.path)

        # Get total already paid
        total_paid = LoanRepayback.objects.filter(loan_request=loan_request).aggregate(
            total=Sum("amount_paid")
        )["total"] or Decimal('0.00')

        remaining_balance = loan_request.approved_amount - total_paid

        if amount_paid > remaining_balance:
            messages.error(request, f"Payment exceeds the remaining balance of ₦{remaining_balance}.")
            return redirect(request.path)

        # Save repayment
        with transaction.atomic():
            new_total_paid = total_paid + amount_paid
            balance_remaining = loan_request.approved_amount - new_total_paid

            LoanRepayback.objects.create(
                loan_request=loan_request,
                amount_paid=amount_paid,
                repayment_date=month,
                balance_remaining=balance_remaining
            )

            # If fully repaid, update status
            if new_total_paid >= loan_request.approved_amount:
                loan_request.status = 'repaid'
                loan_request.save()

        messages.success(request, f"Payment of ₦{amount_paid} recorded successfully for {member}.")
        return redirect(request.path)

    return render(request, "loan/add_single_loan_payment.html")




def upload_loan_repayback(request):
    if request.method == "POST" and request.FILES.get("excel_file"):
        excel_file = request.FILES["excel_file"]

        try:
            # Save file temporarily
            relative_path = default_storage.save(f"upload/{excel_file.name}", ContentFile(excel_file.read()))
            absolute_path = os.path.join(settings.MEDIA_ROOT, relative_path)

            # Read Excel
            df = pd.read_excel(absolute_path, engine="openpyxl")
            default_storage.delete(relative_path)

            if df.empty:
                messages.error(request, "The uploaded file is empty.")
                return redirect(request.path)

            required_columns = {"ippis", "amount", "repayment_date"}
            if not required_columns.issubset(df.columns):
                messages.error(request, "Excel must contain: ippis, amount, repayment_date.")
                return redirect(request.path)

            total_added = 0
            total_skipped = 0
            skipped_entries = []

            with transaction.atomic():
                for _, row in df.iterrows():
                    ippis = row.get("ippis")
                    amount = row.get("amount")
                    repayment_date = row.get("repayment_date")

                    if pd.isna(ippis) or pd.isna(amount) or pd.isna(repayment_date):
                        total_skipped += 1
                        continue

                    try:
                        ippis = int(ippis)
                        amount = Decimal(str(amount))
                        repayment_date = pd.to_datetime(str(repayment_date)).date()
                    except Exception:
                        total_skipped += 1
                        continue

                    member = Member.objects.filter(ippis=ippis).first()
                    if not member:
                        total_skipped += 1
                        continue

                    # Get latest loan request for the member
                    loan = LoanRequest.objects.filter(member=member).order_by('-id').first()
                    if not loan:
                        total_skipped += 1
                        skipped_entries.append(f"{ippis} (no loan)")
                        continue

                    # Calculate remaining balance
                    total_paid = loan.repaybacks.aggregate(total=models.Sum('amount_paid'))['total'] or Decimal('0')
                    remaining_balance = Decimal(str(loan.amount)) - Decimal(str(total_paid))

                    if amount > remaining_balance:
                        total_skipped += 1
                        skipped_entries.append(f"{ippis} (overpayment)")
                        continue

                    # Check if already paid this month
                    already_paid = LoanRepayback.objects.filter(loan_request=loan,repayment_date__year=repayment_date.year,
                        repayment_date__month=repayment_date.month).exists()

                    if already_paid:
                        total_skipped += 1
                        skipped_entries.append(f"{ippis} (already paid {repayment_date.strftime('%B')})")
                        continue

                    # Save repayment
                    LoanRepayback.objects.create(
                        loan_request=loan,
                        repayment_date=repayment_date,
                        amount_paid=amount,
                        balance_remaining=remaining_balance - amount
                    )

                    # Recalculate total paid after this repayment
                    new_total_paid = loan.repaybacks.aggregate(total=models.Sum('amount_paid'))['total'] or Decimal('0')

                    # Compare with approved amount and update loan status
                    if loan.approved_amount and new_total_paid >= Decimal(str(loan.approved_amount)):
                        loan.status = "paid"
                        loan.save()

                    total_added += 1

            if skipped_entries:
                messages.warning(request, f"Skipped IPPIS numbers: {', '.join(skipped_entries)}")

            messages.success(request, f"Upload complete: {total_added} repayments added, {total_skipped} rows skipped.")
            return redirect(request.path)

        except Exception as e:
            traceback.print_exc()
            messages.error(request, f"Error: {e}")
            return redirect(request.path)

    return render(request, "loan/upload_loan_repayback.html")


def get_all_requested_loan(request):
    search_term = request.GET.get('search_term', '').strip()

    base_queryset = LoanRequest.objects.exclude(status='rejected')

    if search_term:
        results_queryset = base_queryset.filter(
            Q(status__icontains=search_term) |
            Q(id__icontains=search_term)
        )
    else:
        results_queryset = base_queryset

    results_queryset = results_queryset.order_by('status')

   
    totals_by_status = dict(
        results_queryset.values('status')
        .annotate(total=Sum('approved_amount'))
        .values_list('status', 'total')
    )

    # Total approved amount (sum of all approved_amount values)
    total_approved_amount = results_queryset.aggregate(total=Sum('approved_amount'))['total'] or 0

    totals_by_status = dict(
        results_queryset.values('status')
        .annotate(total=Sum('amount'))
        .values_list('status', 'total')
    )

    # Total repaid across all filtered loans
    total_repaid = LoanRepayback.objects.filter(
        loan_request__in=results_queryset
    ).aggregate(total=Sum('amount_paid'))['total'] or 0

    # Safe defaults if missing
    total_amont_loan_request = totals_by_status.get('approved', 0)
    total_pending = totals_by_status.get('pending', 0)

    # Pagination
    paginator = Paginator(results_queryset, 100)
    page_number = request.GET.get('page')
    results = paginator.get_page(page_number)

    # PDF export
    if request.GET.get('download_pdf') == '1' and results_queryset.exists():
        if results_queryset.count() > 500:
            return HttpResponse('Too many records to generate PDF. Please narrow your search.', status=400)

        context = {
            'results': results_queryset,
            'search_term': search_term,
            'totals_by_status': totals_by_status,
            'total_approved': total_amont_loan_request,
            'total_pending': total_pending,
            'total_repaid': total_repaid,
            'total_approved_amount': total_approved_amount,
        }
        html = render_to_string('loan/requested_loans_pdf.html', context)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="requested_loans.pdf"'
        pisa_status = pisa.CreatePDF(html, dest=response)
        if pisa_status.err:
            return HttpResponse('Error generating PDF', status=500)
        return response

    # Regular HTML context
    context = {
        'results': results,
        'search_term': search_term,
        'totals_by_status': totals_by_status,
        'total_approved': total_amont_loan_request,
        'total_pending': total_pending,
        'total_repaid': total_repaid,
        'total_approved_amount': total_approved_amount,

    }
    return render(request, 'loan/get_all_requested_loan.html', context)


# def get_all_requested_loan(request):
#     search_term = request.GET.get('search_term', '').strip()

#     base_queryset = LoanRequest.objects.exclude(status='rejected')
#     # approved_amount = LoanRequest.objects.filter(approved_amount='approved_amount')
   
#     if search_term:
#         results_queryset = base_queryset.filter(
#             Q(status__icontains=search_term) |
#             Q(id__icontains=search_term)
#         )
#     else:
#         results_queryset = base_queryset

#     results_queryset = results_queryset.order_by('status')

#     # Totals by status (single query)
#     totals_by_status = dict(
#         results_queryset.values('status')
#         .annotate(total=Sum('approved_amount'))
#         .values_list('status', 'total')
#     )

#     # Pagination
#     paginator = Paginator(results_queryset, 100)
#     page_number = request.GET.get('page')
#     results = paginator.get_page(page_number)

#     # PDF Generation (limit size for performance)
#     if request.GET.get('download_pdf') == '1' and results_queryset.exists():
#         if results_queryset.count() > 500:
#             return HttpResponse('Too many records to generate PDF. Please narrow your search.', status=400)

#         context = {
#             'results': results_queryset,
#             'search_term': search_term,
#             'totals_by_status': totals_by_status,
#         }
#         html = render_to_string('loan/requested_loans_pdf.html', context)
#         response = HttpResponse(content_type='application/pdf')
#         response['Content-Disposition'] = 'attachment; filename="requested_loans.pdf"'
#         pisa_status = pisa.CreatePDF(html, dest=response)
#         if pisa_status.err:
#             return HttpResponse('Error generating PDF', status=500)
#         return response

#     context = {
#         'results': results,
#         'search_term': search_term,
#         'totals_by_status': totals_by_status,
#         # 'approved_amount':approved_amount,
#     }
#     return render(request, 'loan/get_all_requested_loan.html', context)


def edit_requested_loan(request, id):
    loan_types = LoanType.objects.all()
    loanobj = LoanRequest.objects.get(id=id)

    # If user is staff or superuser, use the loan's member
    if request.user.is_staff or request.user.is_superuser:
        member = loanobj.member
    else:
        try:
            member = request.user.member
        except Member.DoesNotExist:
            messages.error(request, "You are not registered as a member.")
            return redirect('some_page')  

        if loanobj.member != member:
            messages.error(request, "You are not allowed to edit this request.")
            return redirect('requested_loan')

    if request.method == "POST":
        loan_type = request.POST['loan_type']
        amount = request.POST['amount']
        loan_term_months = request.POST['loan_term_months']

        LoanRequest.objects.filter(id=id).update(
            member=member,
            loan_type_id=loan_type,
            amount=amount,
            loan_term_months=loan_term_months,
            approved_amount=amount,
        )
        return redirect('requested_loan')

    context = {'loanobj': loanobj, 'loan_types': loan_types}
    return render(request, 'loan/edit_requested_loan.html', context)


def is_admin(user):
    return user.is_staff

@login_required
@user_passes_test(is_admin)
def approve_loan_request(request, id):
    loan_request = get_object_or_404(LoanRequest, id=id, status='pending')
    if request.method == "POST":
        approved_amount = request.POST.get('approved_amount')

        if not approved_amount:
            messages.error(request, "Please enter the approved loan amount.")
            return redirect('approve_loan_request', id=id)

        try:
            approved_amount = float(approved_amount)
            if approved_amount <= 0:
                messages.error(request, "Approved amount must be greater than zero.")
                return redirect('approve_loan_request', id=id)

            if loan_request.loan_type and loan_request.loan_type.max_amount is not None and approved_amount > loan_request.loan_type.max_amount:
                messages.error(request, f"Approved amount cannot exceed the maximum amount for this loan type: {loan_request.loan_type.max_amount}")
                return redirect('approve_loan_request', id=id)

            loan_request.approved_amount = approved_amount
            loan_request.approval_date = timezone.now().date()
            loan_request.status = 'approved'
            loan_request.save()
            messages.success(request, f"Loan request ID {loan_request.id} has been approved for {loan_request.approved_amount}.")
            return redirect('requested_loan') 

        except ValueError:
            messages.error(request, "Invalid approved amount.")
            return redirect('approve_loan_request', id=id)

    context = {'loan_request': loan_request}
    return render(request, 'loan/approve_loan.html', context)


from django.views.decorators.http import require_http_methods

@require_http_methods(["GET", "POST"])
def reject_loan_request(request, id):
    loan_request = LoanRequest.objects.filter(id=id).first()
    if not loan_request:
        messages.error(request, f"No LoanRequest with ID {id} found.")
        return redirect('requested_loan')

    if loan_request.status != 'pending':
        messages.warning(request, f"LoanRequest {id} is already {loan_request.status}. Cannot reject.")
        return redirect('requested_loan')

    if request.method == 'POST':
        reason = request.POST.get('rejection_reason')
        if not reason:
            messages.error(request, "Rejection reason is required.")
            return redirect('reject_loan_request', id=id)

        loan_request.status = 'rejected'
        loan_request.rejection_reason = reason
        loan_request.approval_date = timezone.now().date()
        loan_request.save()

        messages.success(request, f"Loan request ID {loan_request.id} has been rejected with reason.")
        return redirect('requested_loan')

    return render(request, 'loan/reject_loan_form.html', {'loan': loan_request})



def all_reject_loan(request):
    rejected = LoanRequest.objects.filter(status='rejected')
    return render(request,'loan/all_reject_loan.html',{'rejected':rejected} )

def delete_reject_loan(request,id):
    rejectObj = LoanRequest.objects.get(id=id)
    rejectObj.delete()
    return redirect('all_reject_loan')


def loan_years_list(request):
    # Get distinct year and loan_type combinations
    loans = LoanRequest.objects.annotate(year=ExtractYear('application_date')).values('year', 'loan_type__name').distinct().order_by('-year', 'loan_type__name')

    # Structure the data as {2025: ['LONG TERM LOAN'], 2024: ['SHORT TERM LOAN', ...]}
    year_to_loan_types = {}
    for loan in loans:
        year = loan['year']
        loan_type = loan['loan_type__name']
        year_to_loan_types.setdefault(year, []).append(loan_type)

    context = {'year_to_loan_types': year_to_loan_types,}
    return render(request, "loan/loan_years_list.html", context)


# def loans_by_year_and_type(request, year, loan_type_filter):
#     loan_type = get_object_or_404(LoanType, name__iexact=loan_type_filter)
#     loanobj = LoanRequest.objects.filter(loan_type__name = loan_type)
#     print(loanobj)
#     loans = LoanRequest.objects.filter(loan_type=loan_type,application_date__year=year)
    
#     context = {'year': year,'loan_type': loan_type,'loans': loans,'loanobj':loanobj}
#     return render(request, "loan/loans_by_year.html", context)


# def loans_by_year(request, year, loan_type_filter):
#     loan_type = get_object_or_404(LoanType, name__iexact=loan_type_filter)
    
#     # Filter by year and loan_type
#     loanobj = LoanRequest.objects.filter(
#     loan_type=loan_type,
#     date_created__year=year,  # Replace with actual date field if different
#     status='approved'
# )

#     # Totals by status (single query)
#     totals_by_status = dict(
#         loanobj.values('status')
#         .annotate(total=Sum('approved_amount'))
#         .values_list('status', 'total')
#     )
#     print(totals_by_status, 'totals_by_status')

#     context = {
#         'year': year,
#         'loan_type': loan_type,
#         'loanobj': loanobj,
#         'totals_by_status': totals_by_status
#     }
#     return render(request, "loan/loans_by_year.html", context)

def loans_by_year(request, year, loan_type_filter):
    loan_type = get_object_or_404(LoanType, name__iexact=loan_type_filter)
    
    # Filter by year, loan_type, and status
    loanobj = LoanRequest.objects.filter(
        loan_type=loan_type,
        date_created__year=year,
        status='approved'
    )

    # Totals by status
    totals_by_status = dict(
        loanobj.values('status')
        .annotate(total=Sum('approved_amount'))
        .values_list('status', 'total')
    )

    context = {
        'year': year,
        'loan_type': loan_type,
        'loanobj': loanobj,
        'totals_by_status': totals_by_status
    }

    # Check if PDF is requested
    if request.GET.get('download') == 'pdf':
        template_path = 'loan/loans_by_year_pdf.html'  # separate template for PDF
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="loans_{loan_type.name}_{year}.pdf"'

        template = get_template(template_path)
        html = template.render(context)

        pisa_status = pisa.CreatePDF(html, dest=response)

        if pisa_status.err:
            return HttpResponse('We had some errors <pre>' + html + '</pre>')
        return response

    # Default HTML render
    return render(request, "loan/loans_by_year.html", context)
