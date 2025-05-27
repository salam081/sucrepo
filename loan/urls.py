from django.urls import path 
from .import views

urlpatterns = [
    path('add_single_loan_payment', views.add_single_loan_payment, name='add_single_loan_payment'),
    path('upload_loan_repayback', views.upload_loan_repayback, name='upload_loan_repayback'),
    path('requested_loan',views.get_all_requested_loan, name='requested_loan'),
    path('edit_requested_loan/<str:id>/',views.edit_requested_loan,name='edit_requested_loan'),
    path('approve_loan_request/<str:id>/',views.approve_loan_request,name='approve_loan_request'),
    path('reject-loan-request/<int:id>/', views.reject_loan_request, name='reject_loan_request'),
    path('all_reject_loan', views.all_reject_loan, name='all_reject_loan'),
    path('delete_reject_loan/<str:id>/', views.delete_reject_loan, name='delete_reject_loan'),
    path('loan_years_list/', views.loan_years_list, name='loan_years_list'),
    path('loans_by_year/<int:year>/<str:loan_type_filter>/', views.loans_by_year, name='loans_by_year'),
]