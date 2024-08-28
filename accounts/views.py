import json
import uuid
import razorpay
from io import BytesIO
from products.models import *
import xhtml2pdf.pisa as pisa
from django.urls import reverse
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from home.models import ShippingAddress
from django.contrib.auth.models import User
from django.template.loader import get_template
from django.core.validators import validate_email
from accounts.models import Profile, Cart, CartItem
from base.emails import send_account_activation_email
from django.views.decorators.http import require_POST
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, HttpResponse
from django.contrib.auth import authenticate, login, logout
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import redirect, render, get_object_or_404
from accounts.forms import UserUpdateForm, UserProfileForm, ShippingAddressForm, CustomPasswordChangeForm


# Create your views here.


def login_page(request):
    next_url = request.GET.get('next')  # Default to 'index' if 'next' is not provided
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user_obj = User.objects.filter(username=username)

        if not user_obj.exists():
            messages.warning(request, 'Account not found!')
            return HttpResponseRedirect(request.path_info)

        if not user_obj[0].profile.is_email_verified:
            messages.error(request, 'Account not verified!')
            return HttpResponseRedirect(request.path_info)

        # then authenticate user
        user_obj = authenticate(username=username, password=password)
        if user_obj:
            login(request, user_obj)
            messages.success(request, 'Login Successfull.')
            
            # Check if the next URL is safe
            if url_has_allowed_host_and_scheme(url=next_url, allowed_hosts=request.get_host()):
                return redirect(next_url)
            else:
                return redirect('index')

        messages.warning(request, 'Invalid credentials.')
        return HttpResponseRedirect(request.path_info)

    return render(request, 'accounts/login.html')


def register_page(request):
    try:
        if request.method == 'POST':
            username = request.POST.get('username')
            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            email = request.POST.get('email')
            password = request.POST.get('password')
            validate_email(email)

            user_obj = User.objects.filter(username=username, email=email)

            if user_obj.exists():
                messages.info(request, 'Account already exists.')
                return HttpResponseRedirect(request.path_info)

            # if user not registered
            user_obj = User.objects.create(
                first_name=first_name, last_name=last_name, email=email, username=username)
            user_obj.set_password(password)
            user_obj.save()

            email_token = str(uuid.uuid4())
            Profile.objects.create(user=user_obj, email_token=email_token)

            send_account_activation_email(email, email_token)
            messages.success(request, "An email has been sent to your mail.")
            return HttpResponseRedirect(request.path_info)
        
    except Exception:
        messages.error(request, 'Invalid Email Address!')
        return HttpResponseRedirect(request.path_info)

    return render(request, 'accounts/register.html')


@login_required
def user_logout(request):
    logout(request)
    messages.warning(request, "Logged Out Successfully!")
    return redirect('index')


def activate_email_account(request, email_token):
    try:
        user = Profile.objects.get(email_token=email_token)
        user.is_email_verified = True
        user.save()
        messages.success(request, 'Account verification successful.')
        return redirect('login')
    except Exception as e:
        return HttpResponse('Invalid email token.')





@login_required
def add_to_cart(request, uid):
    try:
        variant = request.GET.get('size')
        if not variant:
            messages.error(request, 'Please select a size variant!')
            return redirect(request.META.get('HTTP_REFERER'))
        
        product = get_object_or_404(Product, uid=uid)

        cart, _ = Cart.objects.get_or_create(user=request.user, is_paid=False)
        size_variant = get_object_or_404(SizeVariant, size_name=variant)

        # Check if the cart item already exists in the cart
        cart_item, created = CartItem.objects.get_or_create(cart=cart, product=product, size_variant=size_variant)
        
        if not created:
            cart_item.quantity += 1
            cart_item.save()

        messages.success(request, 'Item added to cart successfully.')

    except Exception as e:
        print(e)
        messages.error(request, 'Error adding item to cart.')

    return redirect(reverse('cart'))


@login_required
def cart(request):
    cart_obj = None
    payment = None
    user = request.user

    try:
        cart_obj = Cart.objects.get(is_paid=False, user=user)

    except Exception as e:
        print(e)
        messages.warning(request, "Your cart is empty. Please sign in or add a product to cart.")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

    if request.method == 'POST':
        coupon = request.POST.get('coupon')
        coupon_obj = Coupon.objects.filter(coupon_code__exact=coupon).first()

        if not coupon_obj:
            messages.warning(request, 'Invalid coupon code.')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

        if cart_obj and cart_obj.coupon:
            messages.warning(request, 'Coupon already exists.')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

        if coupon_obj and coupon_obj.is_expired:
            messages.warning(request, 'Coupon code expired.')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

        if cart_obj and coupon_obj and cart_obj.get_cart_total() < coupon_obj.minimum_amount:
            messages.warning(
                request, f'Amount should be greater than {coupon_obj.minimum_amount}')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

        if cart_obj and coupon_obj:
            cart_obj.coupon = coupon_obj
            cart_obj.save()
            messages.success(request, 'Coupon applied successfully.')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

    if cart_obj:
        
        cart_total_in_paise = int(cart_obj.get_cart_total_price_after_coupon() * 100)
        
        if cart_total_in_paise < 100:
            messages.warning(
                request, 'Total amount in cart is less than the minimum required amount (1.00 INR). Please add a product to the cart.')
            return redirect('index')
        
        client = razorpay.Client(auth = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_SECRET_KEY))
        payment = client.order.create(
            {'amount': cart_total_in_paise, 'currency': 'INR', 'payment_capture': 1})
        cart_obj.razorpay_order_id = payment['id']
        cart_obj.save()

    context = {'cart': cart_obj, 'payment': payment, 'quantity_range': range(1, 6),}
    return render(request, 'accounts/cart.html', context)



@require_POST
@login_required
def update_cart_item(request):
    try:
        data = json.loads(request.body)
        cart_item_id = data.get("cart_item_id")
        quantity = int(data.get("quantity"))

        cart_item = CartItem.objects.get(uid=cart_item_id, cart__user=request.user, cart__is_paid=False)
        cart_item.quantity = quantity
        cart_item.save()

        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


def remove_cart(request, uid):
    try:
        cart_item = get_object_or_404(CartItem, uid=uid)
        cart_item.delete()
        messages.success(request, 'Item removed from cart.')

    except Exception as e:
        print(e)
        messages.warning(request, 'Error removing item from cart.')

    return HttpResponseRedirect(request.META.get('HTTP_REFERER'))


def remove_coupon(request, cart_id):
    cart = Cart.objects.get(uid=cart_id)
    cart.coupon = None
    cart.save()

    messages.success(request, 'Coupon Removed.')
    return HttpResponseRedirect(request.META.get('HTTP_REFERER'))


def success(request):
    order_id = request.GET.get('order_id')
    # cart = Cart.objects.get(razorpay_order_id = order_id)
    cart = get_object_or_404(Cart, razorpay_order_id = order_id)
    cart.is_paid = True
    cart.save()

    context = {'order_id': order_id}
    return render(request, 'payment_success/payment_success.html', context)


# HTML to PDF
def render_to_pdf(template_src, context_dict={}):
    template = get_template(template_src)
    html = template.render(context_dict)
    response = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode('UTF-8')), response)

    file_name = uuid.uuid4()

    try:
        with open(str(settings.BASE_DIR) + f"/public/media/{file_name}.pdf", 'wb+') as output:
            pdf = pisa.pisaDocument(BytesIO(html.encode('UTF-8')), output)
    except Exception as e:
        print(e)

    if pdf.err:
        return HttpResponse("Invalid PDF", status_code=400, content_type='text/plain')
    
    return file_name, True
    

def download_invoice(request, razorpay_order_id):
    order = get_object_or_404(Cart, razorpay_order_id=razorpay_order_id)
    context = {
        'order': order,
        'data': {
            'order_date': order.created_at,
            'name': order.user.get_full_name(),
            'user_email': order.user.email
        }
    }
    pdf = render_to_pdf('pdfs/invoice.html', context)
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="invoice_{razorpay_order_id}.pdf"'
    return response



@login_required
def profile_view(request, username):
    user_name = get_object_or_404(User, username=username)
    user = request.user
    profile = user.profile

    user_form = UserUpdateForm(instance=user)
    profile_form = UserProfileForm(instance=profile)

    if request.method == 'POST':
        user_form = UserUpdateForm(request.POST, instance=user)
        profile_form = UserProfileForm(request.POST, request.FILES, instance=profile)
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, 'Your profile has been updated successfully!')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))

    context = {
        'user_name' : user_name,
        'user_form': user_form,
        'profile_form': profile_form
    }

    return render(request, 'accounts/profile.html', context)


@login_required
def change_password(request):
    if request.method == 'POST':
        form = CustomPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # Important!
            messages.success(request, 'Your password was successfully updated!')
            return HttpResponseRedirect(request.META.get('HTTP_REFERER'))
        else:
            messages.warning(request, 'Please correct the error below.')
    else:
        form = CustomPasswordChangeForm(request.user)
    return render(request, 'accounts/change_password.html', {'form': form})

@login_required
def update_shipping_address(request):
    shipping_address = ShippingAddress.objects.filter(
        user=request.user, current_address=True).first()

    if request.method == 'POST':
        form = ShippingAddressForm(request.POST, instance=shipping_address)
        if form.is_valid():
            shipping_address = form.save(commit=False)
            shipping_address.user = request.user
            shipping_address.current_address = True
            shipping_address.save()

            messages.success(request, "The Address Has Been Successfully Saved/Updated!")
            
            form = ShippingAddressForm()
        else:
            form = ShippingAddressForm(request.POST, instance=shipping_address)
    else:
        form = ShippingAddressForm(instance=shipping_address)

    return render(request, 'accounts/shipping_address_form.html', {'form': form})