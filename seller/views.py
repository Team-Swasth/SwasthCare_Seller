import os
import json
from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import DocumentUploadForm, EditExtractedDataForm
from .azure_services import analyze_and_print_raw_text, extract_structured_data_from_label
from datetime import datetime
from django.http import HttpResponseForbidden
from bson import ObjectId  # newly added import

def seller_required(view_func):
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and hasattr(request.user, 'userprofile') and request.user.userprofile.is_seller:
            return view_func(request, *args, **kwargs)
        return render(request, 'access_denied.html', {
            'message': "Access denied: Seller role required.",
            'redirect_buttons': [
                {'url': 'seller_home', 'label': 'Go to Seller Homepage'},
                {'url': 'consumer_home', 'label': 'Go to Consumer Homepage'}
            ]
        })
    return wrapper

# MongoDB import - conditional based on availability
try:
    import pymongo
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False
    print("Warning: pymongo not available. MongoDB features will be disabled.")

def ddmmyyyy_to_date(date_str):
    try:
        if not date_str:
            return ""
        return datetime.strptime(date_str, "%d-%m-%Y").date()
    except Exception:
        return ""

def date_to_ddmmyyyy(date_val):
    if not date_val:
        return ""
    if isinstance(date_val, str):
        try:
            # If already in DD-MM-YYYY, return as is
            datetime.strptime(date_val, "%d-%m-%Y")
            return date_val
        except Exception:
            pass
        # Try parsing as YYYY-MM-DD
        try:
            return datetime.strptime(date_val, "%Y-%m-%d").strftime("%d-%m-%Y")
        except Exception:
            return str(date_val)
    elif hasattr(date_val, "strftime"):
        return date_val.strftime("%d-%m-%Y")
    return str(date_val)

@login_required
@seller_required
def upload_document(request):
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            file_fields = ["document1", "document2", "document3", "document4"]
            raw_texts = []
            image_urls = []
            media_dir = os.path.join(settings.BASE_DIR, "seller", "static", "seller", "media")
            os.makedirs(media_dir, exist_ok=True)
            for field in file_fields:
                file = request.FILES.get(field)
                if not file:
                    continue
                temp_path = os.path.join(settings.BASE_DIR, "temp_" + file.name)
                with open(temp_path, "wb+") as dest:
                    for chunk in file.chunks():
                        dest.write(chunk)
                raw_text = analyze_and_print_raw_text(temp_path)
                raw_texts.append(raw_text)
                image_path = os.path.join(media_dir, file.name)
                with open(image_path, "wb+") as dest:
                    file.seek(0)
                    for chunk in file.chunks():
                        dest.write(chunk)
                image_urls.append(f"/static/seller/media/{file.name}")
                os.remove(temp_path)
            combined_raw_text = "\n\n".join(raw_texts)
            print(f"Raw Text Extracted")
            structured_data = extract_structured_data_from_label(combined_raw_text)
            # --- Map keys from model output to expected keys ---
            if "manufacturing_date (DD-MM-YYYY)" in structured_data:
                structured_data["manufacturing_date"] = structured_data.pop("manufacturing_date (DD-MM-YYYY)")
            if "allergen_info (camel case)" in structured_data:
                structured_data["allergen_info"] = structured_data.pop("allergen_info (camel case)")
            if "Ingredients (Camel Cased)" in structured_data:
                structured_data["Ingredients"] = structured_data.pop("Ingredients (Camel Cased)")
            expiry = structured_data.get("expiry", {})
            if "tenure (in months)" in expiry:
                expiry["tenure"] = expiry.pop("tenure (in months)")
            if "expiry_date (DD-MM-YYYY)" in expiry:
                expiry["expiry_date"] = expiry.pop("expiry_date (DD-MM-YYYY)")
            structured_data["expiry"] = expiry
            print("GPT 4.1 Processing Complete")
            # Parse DD-MM-YYYY to date objects for the form
            manufacturing_date = ddmmyyyy_to_date(structured_data.get("manufacturing_date", ""))
            expiry_date = ddmmyyyy_to_date(structured_data.get("expiry", {}).get("expiry_date", ""))
            print("Parsed Dates - Manufacturing:", manufacturing_date, "Expiry:", expiry_date)
            nutritional_info = structured_data.get("nutritional_info", {})
            ingredients_val = (
                structured_data.get("ingredients") or
                structured_data.get("Ingredients") or
                structured_data.get("Ingredients (Camel Cased)", "")
            )
            # Convert date objects to DD-MM-YYYY string before putting in session (for JSON serializability)
            initial_data = {
                "prod_name": structured_data.get("prod_name", ""),
                "prod_type": structured_data.get("prod_type", ""),
                "ingredients": ingredients_val,
                "allergen_info": structured_data.get("allergen_info", ""),
                "barcode": structured_data.get("barcode", ""),
                "manufacturing_date": date_to_ddmmyyyy(manufacturing_date),
                "energy_kcal": nutritional_info.get("energy_kcal", ""),
                "protein_g": nutritional_info.get("protein_g", ""),
                "total_fat_g": nutritional_info.get("total_fat_g", ""),
                "saturated_fat_g": nutritional_info.get("saturated_fat_g", ""),
                "trans_fat_g": nutritional_info.get("trans_fat_g", ""),
                "cholesterol_mg": nutritional_info.get("cholesterol_mg", ""),
                "carbohydrate_g": nutritional_info.get("carbohydrate_g", ""),
                "sugars_g": nutritional_info.get("sugars_g", ""),
                "fiber_g": nutritional_info.get("fiber_g", ""),
                "sodium_mg": nutritional_info.get("sodium_mg", ""),
                "calcium_mg": nutritional_info.get("calcium_mg", ""),
                "iron_mg": nutritional_info.get("iron_mg", ""),
                "potassium_mg": nutritional_info.get("potassium_mg", ""),
                "vitamin_c_mg": nutritional_info.get("vitamin_c_mg", ""),
                "vitamin_d_mcg": nutritional_info.get("vitamin_d_mcg", ""),
                "expiry_tenure": structured_data.get("expiry", {}).get("tenure", ""),
                "expiry_date": date_to_ddmmyyyy(expiry_date),
                "special_diet": [],
                "price": structured_data.get("price", ""),
            }
            request.session["edit_form_data"] = initial_data
            request.session["uploaded_image_urls"] = image_urls
            return redirect("edit_extracted_data")
    else:
        form = DocumentUploadForm()
    return render(request, "seller/upload.html", {"form": form})

@login_required
@seller_required
def edit_extracted_data(request):
    initial_data = request.session.get("edit_form_data", {})
    image_urls = request.session.get("uploaded_image_urls", [])
    image_url = image_urls[0] if image_urls else None
    if request.method == "POST":
        form = EditExtractedDataForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data.copy()
            for date_field in ["manufacturing_date", "expiry_date"]:
                val = data.get(date_field)
                data[date_field] = date_to_ddmmyyyy(val)
            nutritional_info = {
                "energy_kcal": data.pop("energy_kcal", None),
                "protein_g": data.pop("protein_g", None),
                "total_fat_g": data.pop("total_fat_g", None),
                "saturated_fat_g": data.pop("saturated_fat_g", None),
                "trans_fat_g": data.pop("trans_fat_g", None),
                "cholesterol_mg": data.pop("cholesterol_mg", None),
                "carbohydrate_g": data.pop("carbohydrate_g", None),
                "sugars_g": data.pop("sugars_g", None),
                "fiber_g": data.pop("fiber_g", None),
                "sodium_mg": data.pop("sodium_mg", None),
                "calcium_mg": data.pop("calcium_mg", None),
                "iron_mg": data.pop("iron_mg", None),
                "potassium_mg": data.pop("potassium_mg", None),
                "vitamin_c_mg": data.pop("vitamin_c_mg", None),
                "vitamin_d_mcg": data.pop("vitamin_d_mcg", None),
            }
            data["nutritional_info"] = nutritional_info
            data["expiry"] = {
                "tenure": data.pop("expiry_tenure", ""),
                "expiry_date": data.pop("expiry_date", ""),
            }
            data["special_diet"] = data.get("special_diet", [])
            # Database operation
            if MONGODB_AVAILABLE:
                client = pymongo.MongoClient(settings.COSMOSDB_URI)
                db = client['swasth']
                collection = db['food']
                editing_item_id = request.session.get("editing_item_id")
                if editing_item_id:
                    collection.update_one({'_id': ObjectId(editing_item_id)}, {'$set': data})
                    messages.success(request, "Product data updated successfully!")
                    del request.session["editing_item_id"]
                else:
                    # Check duplicate barcode and update if exists
                    existing = collection.find_one({'barcode': data.get("barcode", "")})
                    if existing:
                        collection.update_one({'_id': existing['_id']}, {'$set': data})
                        messages.warning(request, "Existing item updated based on duplicate barcode.")
                    else:
                        data["date_uploaded"] = datetime.now()
                        collection.insert_one(data)
                        messages.success(request, "Product data saved successfully to database!")
            else:
                messages.warning(request, "Database not available. Product data not saved.")
                print("MongoDB not available. Product data not saved.")
            
            return render(request, "seller/success.html", {"data": data})
    else:
        for date_field in ["manufacturing_date", "expiry_date"]:
            val = initial_data.get(date_field)
            if isinstance(val, str) and val:
                try:
                    initial_data[date_field] = datetime.strptime(val, "%d-%m-%Y").date()
                except Exception:
                    initial_data[date_field] = ""
        form = EditExtractedDataForm(initial=initial_data)
    return render(request, "seller/edit.html", {"form": form, "image_url": image_url, "image_urls": image_urls})

@login_required
@seller_required
def list_food_items(request):
    food_items = []
    if MONGODB_AVAILABLE:
        try:
            client = pymongo.MongoClient(settings.COSMOSDB_URI)
            db = client['swasth']
            collection = db['food']
            food_items = list(collection.find({}))
            for item in food_items:
                item["id"] = str(item["_id"])  # expose id for template use
        except Exception as e:
            messages.error(request, f"Error fetching food items: {str(e)}")
    return render(request, "seller/food_items.html", {"food_items": food_items})

@login_required
@seller_required
def edit_food_item(request, item_id):
    if MONGODB_AVAILABLE:
        try:
            client = pymongo.MongoClient(settings.COSMOSDB_URI)
            db = client['swasth']
            collection = db['food']
            item = collection.find_one({'_id': ObjectId(item_id)})
            if item:
                # Convert ObjectId to string for JSON serializability
                item["id"] = str(item["_id"])
                del item["_id"]
                # Flatten nutritional_info and expiry for form initial
                nutritional_info = item.get("nutritional_info", {})
                expiry = item.get("expiry", {})
                for k, v in nutritional_info.items():
                    item[k] = v
                item["expiry_tenure"] = expiry.get("tenure", "")
                item["expiry_date"] = expiry.get("expiry_date", "")
                # ...handle other non-serializable fields if any...
                request.session["edit_form_data"] = item
                request.session["uploaded_image_urls"] = []  # optional: set if images are handled
                request.session["editing_item_id"] = item_id
                return redirect("edit_extracted_data")
            else:
                messages.error(request, "Food item not found.")
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")
    return redirect("list_food_items")

@login_required
@seller_required
def seller_home(request):
    """Render the seller homepage."""
    return render(request, 'seller/seller_home.html')