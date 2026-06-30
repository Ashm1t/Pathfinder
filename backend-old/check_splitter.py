try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    print("Found in langchain.text_splitter")
except ImportError:
    print("Not found in langchain.text_splitter")

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    print("Found in langchain_text_splitters")
except ImportError:
    print("Not found in langchain_text_splitters")
