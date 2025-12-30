from django.urls import path
from . import views

urlpatterns = [
    path('add-directive/', views.DirectiveCreateView.as_view(), name='add_directive'),
    path('directives/', views.DirectiveListView.as_view(), name='list_directive'),
    path('directives/<int:pk>/open/', views.directive_open, name='directive_open'),
    path('directives/<int:pk>/delete/', views.DirectiveDeleteView.as_view(), name='directive_delete'),
]
