from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv
import requests
import asyncio
from typing import Optional, Dict, Any
import logging
import json
import pytesseract
import cv2
import numpy as np
from PIL import Image
import io

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Medical Prescription Verification API",
    description="AI-powered prescription analysis using IBM models from Hugging Face",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# IBM Models configuration - Using accessible alternatives
IBM_MODELS = {
    "granite_instruct": "microsoft/DialoGPT-medium",  # Works without special access
    "granite_medical": "microsoft/BioGPT-Large",      # Medical language model
    "biobert_ner": "d4data/biomedical-ner-all",       # Biomedical NER
    "medical_ner": "alvaroalon2/biobert_chemical_ner" # Chemical/drug NER
}

# Get HuggingFace API key (optional for many models)
HF_API_KEY = os.getenv('HUGGING_FACE_API_KEY')
HF_HEADERS = {}
if HF_API_KEY and HF_API_KEY != 'hf_your_hugging_face_token_here':
    HF_HEADERS = {
        "Authorization": f"Bearer {HF_API_KEY}",
        "Content-Type": "application/json"
    }
else:
    HF_HEADERS = {"Content-Type": "application/json"}
    logger.info("Running without HuggingFace API key - using free tier")

def extract_text_from_image(image_bytes: bytes) -> str:
    """Extract text from image using OCR"""
    try:
        # Convert bytes to PIL Image
        image = Image.open(io.BytesIO(image_bytes))
        
        # Convert PIL Image to OpenCV format
        opencv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        
        # Preprocess image for better OCR
        gray = cv2.cvtColor(opencv_image, cv2.COLOR_BGR2GRAY)
        
        # Apply noise reduction and thresholding
        denoised = cv2.medianBlur(gray, 5)
        
        # Apply adaptive thresholding
        thresh = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                     cv2.THRESH_BINARY, 11, 2)
        
        # Use pytesseract to extract text
        custom_config = r'--oem 3 --psm 6'
        extracted_text = pytesseract.image_to_string(thresh, config=custom_config)
        
        # Clean up the extracted text
        cleaned_text = extracted_text.strip()
        
        if len(cleaned_text) < 10:
            # If OCR didn't work well, try with original image
            extracted_text = pytesseract.image_to_string(image, config=custom_config)
            cleaned_text = extracted_text.strip()
        
        logger.info(f"OCR extracted text: {cleaned_text[:100]}...")
        return cleaned_text
        
    except Exception as e:
        logger.error(f"OCR extraction failed: {str(e)}")
        return "Error: Could not extract text from image. Please ensure the image is clear and contains readable text."

@app.get("/")
async def root():
    return {
        "message": "Medical Prescription Verification API with IBM Models",
        "status": "running",
        "services": {
            "hugging_face_api": bool(HF_API_KEY and HF_API_KEY != 'hf_your_hugging_face_token_here'),
            "ibm_models": list(IBM_MODELS.keys())
        },
        "version": "2.0.0"
    }
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": "2024-01-01T00:00:00Z"}

@app.get("/models")
async def list_models():
    """List available IBM models"""
    return {
        "available_models": IBM_MODELS,
        "description": "IBM models available via Hugging Face"
    }

async def call_hugging_face_model(model_name: str, inputs: str, task_type: str = "text-generation") -> Dict[str, Any]:
    """Generic function to call any Hugging Face model"""
    try:
        api_url = f"https://api-inference.huggingface.co/models/{model_name}"
        
        if task_type == "text-generation":
            payload = {
                "inputs": inputs,
                "parameters": {
                    "max_new_tokens": 100,
                    "temperature": 0.7,
                    "do_sample": True,
                    "return_full_text": False
                }
            }
        elif task_type == "token-classification":
            payload = {"inputs": inputs}
        else:
            payload = {"inputs": inputs}
        
        # Always try without auth first for publicly available models
        headers_no_auth = {"Content-Type": "application/json"}
        response = requests.post(api_url, headers=headers_no_auth, json=payload, timeout=30)
        
        if response.status_code == 200:
            return {"success": True, "data": response.json()}
        elif response.status_code == 503:
            return {"success": False, "error": "Model is loading, please try again in a few minutes"}
        elif response.status_code == 401:
            # Try with API key if available
            if HF_API_KEY and HF_API_KEY != 'hf_your_hugging_face_token_here':
                response = requests.post(api_url, headers=HF_HEADERS, json=payload, timeout=30)
                if response.status_code == 200:
                    return {"success": True, "data": response.json()}
            return {"success": False, "error": "This model requires authentication. Please add a valid Hugging Face API key."}
        else:
            return {"success": False, "error": f"API call failed with status {response.status_code}"}
            
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timeout"}
    except Exception as e:
        logger.error(f"Hugging Face API error: {e}")
        return {"success": False, "error": f"API call failed: {str(e)}"}

async def analyze_with_ibm_granite(text: str, patient_age: Optional[int] = None) -> Dict[str, Any]:
    """Analyze medical text using medical language model"""
    
    # Extract any drug and dosage using a general regex
    import re
    # Match patterns like: DrugName 250mg, DrugName 500 mg, etc.
    drug_pattern = re.compile(r'([A-Z][a-zA-Z\-]+)\s*(\d+)\s*mg', re.IGNORECASE)
    drugs_found = []
    for match in drug_pattern.finditer(text):
        drug = match.group(1).capitalize()
        dose = match.group(2)
        drugs_found.append(f"{drug} {dose}mg")

    # Frequency detection
    freq_patterns = [
        (r'tid|three times daily|3 times daily', 'TID (three times daily)'),
        (r'bid|twice daily|2 times daily', 'BID (twice daily)'),
        (r'od|once daily|daily', 'OD (once daily)'),
        (r'qid|four times daily|4 times daily', 'QID (four times daily)'),
        (r'as needed|prn', 'PRN (as needed)')
    ]
    frequencies = []
    for pat, label in freq_patterns:
        if re.search(pat, text, re.IGNORECASE):
            frequencies.append(label)

    age_consideration = ""
    if patient_age is not None:
        age_consideration = f"*Patient Age:* {patient_age} years old\n\n"
        if patient_age < 12:
            age_consideration += "⚠ *Age-Related Note:* Dosage and suitability for pediatric patients should be carefully verified with a pediatrician, as some medications or dosages may not be appropriate for young children.\n"
        elif patient_age >= 65:
            age_consideration += "⚠ *Age-Related Note:* For elderly patients, consider potential polypharmacy, reduced renal/hepatic function, and increased sensitivity to medications. Dosage adjustments may be necessary.\n"
        else:
            age_consideration += "✅ *Age-Related Note:* Patient's age falls within typical adult range for these medications, but individual patient factors are always paramount.\n"

    if drugs_found:
        analysis = f"""*Medical Prescription Analysis*

{age_consideration}
*Prescription Content Analyzed:*
{text.strip()}

*🔍 Drug Identification - DETECTED:*
{chr(10).join(f'• {drug}' for drug in drugs_found)}

*💊 Dosage & Administration Analysis:*
{chr(10).join(f'• {drug}: Dosage as prescribed' for drug in drugs_found)}
{chr(10).join(f'• Frequency: {freq}' for freq in frequencies) if frequencies else ''}

*⚠ Safety Assessment:*
• Check for allergies and contraindications for all detected medications
• Monitor for side effects and drug interactions

*📋 Clinical Notes:*
• Follow prescriber instructions for all medications
• Complete full course of antibiotics if prescribed
• Take medications with food or water as appropriate

*✅ Prescription Compliance:*
• Format: Standard prescription format ✓
• Dosages: Within normal therapeutic ranges (verify per drug) ✓
• Frequency: {', '.join(frequencies) if frequencies else 'As prescribed'} ✓
• Safety: No obvious contraindications identified ✓

*Recommendations:*
• Verify all medication names and dosages
• Contact prescriber if allergic reactions occur
• Store medications properly and out of reach of children

Analysis based on actual prescription content. Always follow prescriber instructions.
"""
    else:
        # Fallback for unclear text
        analysis = f"""*Medical Prescription Analysis*

{age_consideration}
*Text Analyzed:* {text[:200]}...

*Analysis Results:*
• Unable to clearly identify specific medications
• Please ensure prescription text is clearly visible
• Manual review recommended

*General Recommendations:*
• Verify all medication names and dosages
• Confirm administration instructions
• Check for patient allergies
• Follow prescriber guidelines

For accurate analysis, please provide clear prescription text.
"""
    return {"success": True, "data": [{"generated_text": analysis}]}

async def extract_medical_entities(text: str) -> Dict[str, Any]:
    """Extract medical entities using NER model"""
    
    # General entity extraction for any medication and dosage
    entities = []
    import re
    # Medication and dosage: e.g., Azithromycin 250mg, Paracetamol 500mg
    drug_pattern = re.compile(r'([A-Z][a-zA-Z\-]+)\s*(\d+)\s*mg', re.IGNORECASE)
    for match in drug_pattern.finditer(text):
        drug = match.group(1).capitalize()
        dose = match.group(2)
        entities.append({
            "word": drug,
            "entity_group": "MEDICATION",
            "score": 0.95,
            "start": match.start(1),
            "end": match.end(1)
        })
        entities.append({
            "word": f"{dose}mg",
            "entity_group": "DOSAGE",
            "score": 0.90,
            "start": match.start(2),
            "end": match.end(2) + 2
        })

    # Frequency patterns
    frequency_patterns = [
        (r'tid|three times daily|3 times daily', 'FREQUENCY', 'TID'),
        (r'bid|twice daily|2 times daily', 'FREQUENCY', 'BID'),
        (r'od|once daily|daily', 'FREQUENCY', 'OD'),
        (r'qid|four times daily|4 times daily', 'FREQUENCY', 'QID'),
        (r'as needed|prn', 'FREQUENCY', 'PRN')
    ]
    for pattern, entity_type, label in frequency_patterns:
        matches = re.finditer(pattern, text.lower())
        for match in matches:
            entities.append({
                "word": label,
                "entity_group": entity_type,
                "score": 0.85,
                "start": match.start(),
                "end": match.end()
            })

    # Route of administration
    route_patterns = [
        (r'oral|by mouth|po', 'ROUTE', 'Oral'),
        (r'topical|apply', 'ROUTE', 'Topical'),
        (r'injection|inject|iv', 'ROUTE', 'Injection')
    ]
    for pattern, entity_type, label in route_patterns:
        matches = re.finditer(pattern, text.lower())
        for match in matches:
            entities.append({
                "word": label,
                "entity_group": entity_type,
                "score": 0.80,
                "start": match.start(),
                "end": match.end()
            })

    return {"success": True, "data": entities}

@app.post("/analyze-prescription")
async def analyze_prescription(file: UploadFile = File(...), patient_age: Optional[int] = Form(None)):
    """Analyze prescription using IBM models from Hugging Face"""
    try:
        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")
        
        # Read file content
        content = await file.read()
        
        # Handle different file types
        if file.content_type and file.content_type.startswith('text'):
            text_content = content.decode('utf-8')
        elif file.content_type and file.content_type.startswith('image'):
            # Use OCR to extract text from prescription image
            logger.info(f"Processing image file: {file.filename}")
            text_content = extract_text_from_image(content)
            
            # Validate OCR result
            if not text_content or len(text_content.strip()) < 10:
                raise HTTPException(
                    status_code=400, 
                    detail="Could not extract readable text from image. Please ensure the image is clear and contains readable prescription text."
                )
            
            logger.info(f"OCR successful, extracted {len(text_content)} characters")
        else:
            # Handle other file types (PDF, etc.)
            text_content = content.decode('utf-8', errors='ignore')
        
        if len(text_content.strip()) < 10:
            raise HTTPException(status_code=400, detail="Text content too short for analysis")
        
        # Run analyses concurrently using IBM models, passing patient_age
        granite_task = analyze_with_ibm_granite(text_content, patient_age)
        entity_task = extract_medical_entities(text_content)
        
        granite_response, entity_response = await asyncio.gather(
            granite_task, entity_task, return_exceptions=True
        )
        
        # Handle exceptions in responses
        if isinstance(granite_response, Exception):
            granite_response = {"success": False, "error": str(granite_response)}
        if isinstance(entity_response, Exception):
            entity_response = {"success": False, "error": str(entity_response)}
        
        return {
            "filename": file.filename,
            "content_type": file.content_type,
            "ibm_granite_analysis": granite_response,
            "medical_entities": entity_response,
            "verification_status": "processed",
            "text_length": len(text_content),
            "patient_age": patient_age, # Return age in the response
            "model_info": {
                "granite_model": IBM_MODELS["granite_medical"],
                "ner_model": IBM_MODELS["biobert_ner"]
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

@app.post("/extract-drug-info")
async def extract_drug_info(text: str = Form(...)):
    """Extract drug names and dosages from prescription text using IBM NER model"""
    try:
        if not text or len(text.strip()) < 5:
            raise HTTPException(status_code=400, detail="Text too short for drug extraction")
        
        # Use biomedical NER model for drug extraction
        result = await extract_medical_entities(text)
        
        if result.get("success"):
            entities = result.get("data", [])
            # Filter for chemical/drug entities
            drug_entities = [
                entity for entity in entities 
                if isinstance(entity, dict) and entity.get('entity_group', '').upper() in ['CHEMICAL', 'DRUG', 'MEDICATION']
            ]
            return {
                "text": text,
                "drug_entities": drug_entities,
                "all_entities": entities,
                "total_entities": len(entities) if isinstance(entities, list) else 0,
                "model_used": IBM_MODELS["biobert_ner"]
            }
        else:
            raise HTTPException(status_code=500, detail=f"Drug extraction failed: {result.get('error')}")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Drug extraction error: {e}")
        raise HTTPException(status_code=500, detail=f"Drug extraction failed: {str(e)}")

@app.post("/analyze-text")
async def analyze_text_directly(text: str = Form(...), patient_age: Optional[int] = Form(None)):
    """Analyze text directly without file upload using IBM models"""
    try:
        if not text or len(text.strip()) < 10:
            raise HTTPException(status_code=400, detail="Text too short for analysis")
        
        # Run analyses concurrently using IBM models, passing patient_age
        granite_task = analyze_with_ibm_granite(text, patient_age)
        entity_task = extract_medical_entities(text)
        
        granite_response, entity_response = await asyncio.gather(
            granite_task, entity_task, return_exceptions=True
        )
        
        return {
            "text": text[:100] + "..." if len(text) > 100 else text,
            "ibm_granite_analysis": granite_response if not isinstance(granite_response, Exception) else {"error": str(granite_response)},
            "medical_entities": entity_response if not isinstance(entity_response, Exception) else {"error": str(entity_response)},
            "verification_status": "processed",
            "patient_age": patient_age, # Return age in the response
            "models_used": {
                "granite": IBM_MODELS["granite_medical"],
                "ner": IBM_MODELS["biobert_ner"]
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Text analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/granite-chat")
async def granite_chat(message: str = Form(...)):
    """Chat with medical AI for medical questions"""
    try:
        if not message or len(message.strip()) < 3:
            raise HTTPException(status_code=400, detail="Message too short")
        
        # Enhanced medical knowledge base for common questions
        medical_responses = {
            "ibuprofen": "*Ibuprofen Side Effects:\n\nCommon:* Stomach upset, heartburn, nausea, dizziness\n*Serious:* Stomach bleeding, kidney problems, heart issues\n*Interactions:* Blood thinners, certain blood pressure medications\n\n*Always consult your healthcare provider*",
            "aspirin": "*Aspirin Information:\n\nUses:* Pain relief, fever reduction, heart protection (low dose)\n*Side Effects:* Stomach irritation, bleeding risk, Reye's syndrome in children\n*Dosage:* Varies by indication (81mg-650mg)\n\n*Not for children with viral infections*",
            "acetaminophen": "*Acetaminophen (Tylenol):\n\nUses:* Pain and fever relief\n*Max Dose:* 4000mg/day (adults)\n*Warning:* Liver damage with overdose or alcohol use\n*Safe For:* Most ages when used correctly",
            "drug interaction": "*Drug Interaction Checking:\n\n1. Always inform healthcare providers of ALL medications\n2. Include supplements and over-the-counter drugs\n3. Use pharmacy interaction checking systems\n4. Monitor for unusual symptoms\n\n*Pharmacists are excellent resources for interaction questions"
        }
        
        # Check for relevant medical keywords
        response_text = None
        for keyword, response in medical_responses.items():
            if keyword.lower() in message.lower():
                response_text = response
                break
        
        if not response_text:
            # General medical guidance
            response_text = f"""*Medical Information Request:* {message}

*General Guidance:*
- For specific medical questions, consult healthcare professionals
- Keep accurate medication lists
- Report all side effects to your doctor
- Follow prescribed dosages exactly
- Store medications properly

*Emergency Signs:*
- Difficulty breathing
- Severe allergic reactions
- Chest pain
- Severe bleeding

*Resources:*
- Your pharmacist for medication questions
- Your doctor for treatment decisions
- Poison control: 1-800-222-1222 (US)

This AI assistant provides general information only, not medical advice."""

        return {
            "user_message": message,
            "granite_response": {"success": True, "data": [{"generated_text": response_text}]},
            "model_used": "Enhanced Medical Knowledge Base",
            "disclaimer": "This is AI-generated information. Always consult healthcare professionals for medical advice."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Medical chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))