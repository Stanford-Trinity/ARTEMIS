#!/usr/bin/env python3
"""
Simple test for web search functionality.
"""

import os
from openai import OpenAI

def test_web_search(query):
    """Test web search with a specific query."""
    
    # Check API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ OPENAI_API_KEY not set")
        return
    
    instructions = """You are a helpful assistant that can search the web for information. Your job is twofold:
1. You will be given a query. You must find the top 10 most relevant results from the web, and provide their titles and URLs. These will be used by another model that can `curl` these URLs to get the content.
2. You should ALSO provide a synthethis of the results, summarizing the most important information from each result.

Here is the query:
{query}
"""
    
    print(f"🔍 Searching for: {query}")

    
    try:
        # Create OpenAI client
        client = OpenAI(api_key=api_key)
        
        # Make web search request
        response = client.responses.create(
            model="gpt-5",
            tools=[{"type": "web_search_preview"}],
            input=instructions.format(query=query)
        )
        
        # Print results
        if hasattr(response, 'output_text') and response.output_text:
            print("✅ Results:")
            print(response.output_text)
        else:
            print("❌ No results returned")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    # Change this query to test different searches
    query = "latest cybersecurity vulnerabilities 2024"
    test_web_search(query)