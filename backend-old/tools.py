from langchain.tools import tool
import os
import shutil
from typing import List

@tool
def list_files(directory_path: str) -> List[str]:
    """Lists all files in a given directory."""
    try:
        return os.listdir(directory_path)
    except Exception as e:
        return [f"Error: {str(e)}"]

@tool
def organize_files(directory_path: str, strategy: str = "by_type") -> str:
    """
    Organizes files in a directory.
    Strategy 'by_type': Moves files into folders like 'Images', 'Documents', etc.
    """
    if not os.path.exists(directory_path):
        return "Directory not found."
        
    try:
        for filename in os.listdir(directory_path):
            file_path = os.path.join(directory_path, filename)
            if os.path.isfile(file_path):
                ext = os.path.splitext(filename)[1].lower()
                folder_name = "Others"
                
                if ext in ['.jpg', '.png', '.jpeg']: folder_name = "Images"
                elif ext in ['.pdf', '.docx', '.txt']: folder_name = "Documents"
                elif ext in ['.mp4', '.avi']: folder_name = "Videos"
                
                target_folder = os.path.join(directory_path, folder_name)
                os.makedirs(target_folder, exist_ok=True)
                shutil.move(file_path, os.path.join(target_folder, filename))
                
        return "Files organized successfully."
    except Exception as e:
        return f"Error organizing files: {str(e)}"

@tool
def read_file_content(file_path: str) -> str:
    """Reads the content of a text file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"

@tool
def draft_document(template_type: str, case_details: str) -> str:
    """
    Drafts a legal document based on a template type and case details.
    Returns the drafted text.
    """
    # Mock implementation - in real world, this would use an LLM to generate text
    return f"DRAFT DOCUMENT: {template_type}\n\nBased on case: {case_details}\n\n[Legal content placeholder...]"
