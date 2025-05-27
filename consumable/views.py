from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db.models import Prefetch
from .models import *
from loan.models import *
from django.db.models import Q
from django.core.paginator import Paginator
import pandas as pd
from django.db import transaction
from decimal import Decimal
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import os
from django.conf import settings
from django.utils.dateparse import parse_date

from django.contrib.admin.views.decorators import staff_member_required



def consumable_requests_list(request):
    if request.method == 'POST':
        if 'remove_item_id' in request.POST:
            detail_id = request.POST.get('remove_item_id')
            detail = get_object_or_404(ConsumableRequestDetail, id=detail_id)
            if detail.request.status == 'Pending':
                detail.delete()
                messages.success(request, "Item removed successfully.")
            else:
                messages.error(request, "Cannot remove item from an approved request.")
            return redirect('consumable_requests_list')

        elif 'reduce_quantity_id' in request.POST:
            detail_id = request.POST.get('reduce_quantity_id')
            new_quantity = request.POST.get('new_quantity')
            detail = get_object_or_404(ConsumableRequestDetail, id=detail_id)

            if detail.request.status != 'Pending':
                messages.error(request, "Cannot reduce quantity on an approved request.")
            elif new_quantity.isdigit() and 0 < int(new_quantity) < detail.quantity:
                detail.quantity = int(new_quantity)
                detail.save()
                messages.success(request, "Quantity reduced successfully.")
            elif int(new_quantity) == detail.quantity:
                messages.info(request, "Quantity is unchanged.")
            else:
                messages.error(request, "Invalid quantity. Must be lower than current value and greater than 0.")
            return redirect('consumable_requests_list')

    search_query = request.GET.get('q', '').strip()
    requests = ConsumableRequest.objects.all()

    if search_query:
        requests = requests.filter(
            Q(user__first_name__icontains=search_query) | 
            Q(user__last_name__icontains=search_query) | 
            Q(user__username__icontains=search_query) | 
            Q(status__iexact=search_query) | 
            Q(details__item__title__icontains=search_query)
        ).distinct()

    requests = requests.prefetch_related(
        Prefetch('details__item')
    ).order_by('-date_created')

    paginator = Paginator(requests, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'search_query': search_query
    }
    return render(request, 'consumables/request_list.html', context)




@staff_member_required
def admin_edit_consumable_request(request, request_id):
    consumable_request = get_object_or_404(ConsumableRequest, id=request_id, status='Pending')
    items = Item.objects.filter(available=True)
    details = ConsumableRequestDetail.objects.filter(request=consumable_request)

    # Only allow the admin to change the loan term
    if request.method == 'POST':
        loan_term_months = request.POST.get('loan_term_months')

        if not loan_term_months or not loan_term_months.isdigit() or int(loan_term_months) <= 0:
            messages.error(request, "Please provide a valid loan term.")
            return render(request, 'consumables/admin_edit_request.html', {
                'items': items,
                'details': details,
                'request_obj': consumable_request
            })

        try:
            with transaction.atomic():
                # Update the loan term for the existing request
                for detail in details:
                    detail.loan_term_months = int(loan_term_months)
                    detail.save()

            messages.success(request, "Loan term updated successfully.")
            return redirect('admin_pending_requests')

        except Exception as e:
            # messages.error(request, f"An error occurred: {e}")
            return render(request, 'consumables/admin_edit_request.html', {
                'items': items,
                'details': details,
                'request_obj': consumable_request
            })

    return render(request, 'consumables/admin_edit_request.html', {
        'items': items,
        'details': details,
        'request_obj': consumable_request
    })




def approve_consumable_request(request, request_id):
    consumable_request = get_object_or_404(ConsumableRequest, id=request_id)
    
    if consumable_request.status != 'Pending':
        messages.warning(request, 'This request is already processed.')
    else:
        consumable_request.status = 'Approved'
        consumable_request.save()
        messages.success(request, 'The request has been approved.')
    
    return redirect('consumable_requests_list')



def decline_consumable_request(request, request_id):
    consumable_request = get_object_or_404(ConsumableRequest, id=request_id)
    
    if consumable_request.status != 'Pending':
        messages.warning(request, 'This request is already processed.')
    else:
        consumable_request.status = 'Declined'
        consumable_request.save()
        messages.success(request, 'The request has been declined.')
    
    return redirect('consumable_requests_list')



def upload_consumable_payment(request):
    if request.method == "POST" and request.FILES.get("excel_file"):
        excel_file = request.FILES["excel_file"]

        try:
            # Save file temporarily
            relative_path = default_storage.save(f"upload/{excel_file.name}", ContentFile(excel_file.read()))
            absolute_path = os.path.join(settings.MEDIA_ROOT, relative_path)

            # Read Excel
            df = pd.read_excel(absolute_path, engine="openpyxl")
            default_storage.delete(relative_path)

            # Normalize column names
            df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")

            if df.empty:
                messages.error(request, "The uploaded file is empty.")
                return redirect(request.path)

            required_columns = {"ippis", "amount_paid", "month"}
            if not required_columns.issubset(df.columns):
                messages.error(request, "Excel must contain: ippis, amount_paid, month.")
                return redirect(request.path)

            total_added = 0
            total_skipped = 0
            skipped_entries = []

            with transaction.atomic():
                for _, row in df.iterrows():
                    ippis = row.get("ippis")
                    amount_paid = row.get("amount_paid")
                    month = row.get("month")

                    if pd.isna(ippis) or pd.isna(amount_paid) or pd.isna(month):
                        total_skipped += 1
                        continue

                    try:
                        ippis = int(ippis)
                        amount_paid = Decimal(str(amount_paid))
                        month = pd.to_datetime(str(month)).date()  # Ensure correct date format
                    except Exception as e:
                        total_skipped += 1
                        skipped_entries.append(f"{ippis} (Invalid data: {str(e)})")
                        continue

                    # Find the user by IPPIS
                    # user = User.objects.filter(ippis=ippis).first()
                    user = User.objects.filter(member__ippis=ippis).first()
                    if not user:
                        total_skipped += 1
                        skipped_entries.append(f"{ippis} (User not found)")
                        continue

                    # Get the latest consumable request for the user
                    consumable_request = ConsumableRequest.objects.filter(user=user, status='Approved').first()
                    if not consumable_request:
                        total_skipped += 1
                        skipped_entries.append(f"{ippis} (no approved request)")
                        continue

                    # Calculate remaining balance
                    total_paid = PaybackConsumable.objects.filter(
                        consumable_request=consumable_request
                    ).aggregate(total=models.Sum('amount_paid'))['total'] or Decimal('0')
                    remaining_balance = consumable_request.calculate_total_price() - total_paid

                    if amount_paid > remaining_balance:
                        total_skipped += 1
                        skipped_entries.append(f"{ippis} (overpayment)")
                        continue

                    # Check if already paid this month
                    already_paid = PaybackConsumable.objects.filter(
                        consumable_request=consumable_request,
                        repayment_date__year=month.year,
                        repayment_date__month=month.month
                    ).exists()

                    if already_paid:
                        total_skipped += 1
                        skipped_entries.append(f"{ippis} (already paid for {month.strftime('%B')})")
                        continue

                    # Save repayment
                    PaybackConsumable.objects.create(
                        consumable_request=consumable_request,
                        amount_paid=amount_paid,
                        balance_remaining=remaining_balance - amount_paid,
                        repayment_date=month
                    )

                    # Recalculate total paid after this repayment
                    new_total_paid = PaybackConsumable.objects.filter(
                        consumable_request=consumable_request
                    ).aggregate(total=models.Sum('amount_paid'))['total'] or Decimal('0')

                    # Update request status if fully paid
                    if new_total_paid >= consumable_request.calculate_total_price():
                        consumable_request.status = 'Paid'
                        consumable_request.save()

                    total_added += 1

            if skipped_entries:
                messages.warning(request, f"Skipped IPPIS numbers: {', '.join(skipped_entries)}")

            messages.success(request, f"Upload complete: {total_added} payments added, {total_skipped} rows skipped.")
            return redirect(request.path)

        except Exception as e:
            messages.error(request, f"Error: {e}")
            return redirect(request.path)

    return render(request, "consumables/upload_consumable_payment.html")



def add_single_consumable_payment(request):
    if request.method == "POST":
        ippis = request.POST.get("ippis")
        amount_paid = request.POST.get("amount_paid")
        month = request.POST.get("month")  # Expected format: YYYY-MM-DD

        if not ippis or not amount_paid or not month:
            messages.error(request, "All fields are required.")
            return redirect(request.path)

        try:
            ippis = int(ippis)
            amount_paid = Decimal(amount_paid)
            month = parse_date(month)
            if not month:
                raise ValueError("Invalid date format.")
        except Exception as e:
            messages.error(request, f"Invalid input: {e}")
            return redirect(request.path)

        member = Member.objects.filter(ippis=ippis).first()
        if not member or not member.member:
            messages.error(request, f"No user found with IPPIS: {ippis}")
            return redirect(request.path)

        user = member.member
        consumable_request = ConsumableRequest.objects.filter(user=user, status='Approved').first()
        if not consumable_request:
            messages.error(request, "No approved consumable request for this user.")
            return redirect(request.path)

        total_paid = PaybackConsumable.objects.filter(consumable_request=consumable_request).aggregate(
            total=models.Sum("amount_paid")
        )["total"] or Decimal('0')

        remaining_balance = consumable_request.calculate_total_price() - total_paid

        if amount_paid > remaining_balance:
            messages.error(request, "Payment exceeds remaining balance.")
            return redirect(request.path)

        already_paid = PaybackConsumable.objects.filter(
            consumable_request=consumable_request,
            repayment_date__year=month.year,
            repayment_date__month=month.month
        ).exists()

        if already_paid:
            messages.warning(request, f"Payment already exists for {month.strftime('%B')}.")
            return redirect(request.path)

        with transaction.atomic():
            PaybackConsumable.objects.create(
                consumable_request=consumable_request,
                amount_paid=amount_paid,
                repayment_date=month
            )

            new_total_paid = PaybackConsumable.objects.filter(consumable_request=consumable_request).aggregate(
                total=models.Sum('amount_paid')
            )['total'] or Decimal('0')

            if new_total_paid >= consumable_request.calculate_total_price():
                consumable_request.status = 'Paid'
                consumable_request.save()

        messages.success(request, f"Payment of ₦{amount_paid} recorded for {user.first_name} ({ippis}).")
        return redirect(request.path)

    return render(request, "consumables/add_single_payment.html")




