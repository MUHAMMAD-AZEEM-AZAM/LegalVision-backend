from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import os
import logging
import asyncio
import json
from typing import Optional, List
from dotenv import load_dotenv

# Import our custom modules
from LegalAIAgent import LegalAIAgent
from services.document_processor import DocumentProcessor
from services.image_processor import ImageProcessor

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Legal AI Chat API",
    description="API for legal document analysis and chat with AI agent",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize processors
document_processor = DocumentProcessor()
image_processor = ImageProcessor()

# Initialize Legal AI Agent
try:
    search_api_key = os.getenv("SEARCH_API_KEY")
    mongo_connection_string = os.getenv("MONGODB_URI")
    
    if not search_api_key or not mongo_connection_string:
        raise ValueError("Missing required environment variables")
    
    legal_agent = LegalAIAgent(
        search_api_key=search_api_key,
        mongo_connection_string=mongo_connection_string
    )
    logger.info("Legal AI Agent initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Legal AI Agent: {e}")
    legal_agent = None

# Pydantic models
class ChatRequest(BaseModel):
    message: str
    context: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    document_text: Optional[str] = None
    image_text: Optional[str] = None
    prompt: str
    word_count: Optional[int] = None
    agent_actions: Optional[list] = None

class AgentAction(BaseModel):
    action: str
    description: str
    timestamp: str

class HealthResponse(BaseModel):
    status: str
    agent_ready: bool

# Health check endpoint
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check API health and agent status"""
    return HealthResponse(
        status="healthy",
        agent_ready=legal_agent is not None
    )

# Real-time agent actions stream
@app.get("/chat/actions/stream")
async def stream_agent_actions():
    """Stream agent actions in real-time using Server-Sent Events"""
    
    async def event_stream():
        while True:
            if legal_agent:
                actions = legal_agent.get_agent_actions()
                if actions:
                    # Send latest actions
                    yield f"data: {json.dumps(actions)}\n\n"
            
            await asyncio.sleep(1)  # Check every second
    
    return StreamingResponse(
        event_stream(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream"
        }
    )

# Unified chat endpoint
@app.post("/chat", response_model=ChatResponse)
async def chat_with_agent(
    message: str = Form(...),
    document: Optional[UploadFile] = File(None),
    image: Optional[UploadFile] = File(None),
    context: Optional[str] = Form(None)
):
    """
    Unified chat endpoint with Legal AI Agent
    
    - message: Text message from user (required)
    - document: Optional document file (PDF, DOCX, TXT)
    - image: Optional image file (JPG, PNG, BMP, TIFF, WEBP)
    - context: Optional additional text context
    """
    
    if not legal_agent:
        raise HTTPException(status_code=503, detail="Legal AI Agent not available")
    
    try:
        # Clear previous agent actions
        legal_agent.clear_agent_actions()
        
        document_text = None
        image_text = None
        total_word_count = 0
        
        # Process document if provided
        if document:
            logger.info(f"Processing document: {document.filename}")
            document_text = await document_processor.process_document(document)
            if document_text:
                total_word_count += len(document_text.split())
        
        # Process image if provided
        if image:
            logger.info(f"Processing image: {image.filename}")
            image_text = await image_processor.process_image(image)
            if image_text:
                total_word_count += len(image_text.split())
        
        # Add context word count if provided
        if context:
            total_word_count += len(context.split())
        
        # Check word limit
        if total_word_count > 500:
            raise HTTPException(
                status_code=400, 
                detail=f"Total content too large ({total_word_count} words). Maximum allowed: 500 words."
            )
        
        # Build final prompt
        prompt_parts = []
        
        if document_text:
            prompt_parts.append(f"Document content:\n{document_text}")
        
        if image_text:
            prompt_parts.append(f"Image content:\n{image_text}")
        
        if context:
            prompt_parts.append(f"Additional context:\n{context}")
        
        if prompt_parts:
            final_prompt = f"{'\n\n'.join(prompt_parts)}\n\nUser question: {message}"
        else:
            final_prompt = message
        
        # Get response from Legal AI Agent
        logger.info("Sending request to Legal AI Agent")
        response = legal_agent.chat_with_agent(final_prompt)
        
        return ChatResponse(
            response=response,
            document_text=document_text,
            image_text=image_text,
            prompt=final_prompt,
            word_count=total_word_count if total_word_count > 0 else None,
            agent_actions=legal_agent.get_agent_actions()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Clear conversation history
@app.post("/chat/clear")
async def clear_conversation():
    """Clear the conversation history"""
    
    if not legal_agent:
        raise HTTPException(status_code=503, detail="Legal AI Agent not available")
    
    try:
        legal_agent.clear_history()
        return {"message": "Conversation history cleared successfully"}
    except Exception as e:
        logger.error(f"Error clearing conversation: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
