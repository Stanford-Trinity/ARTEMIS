tests/test_todo_generation.py#!/usr/bin/env python3
"""
Test script for TODO generation functionality
"""

import asyncio
import sys
import os
from pathlib import Path

# Add the parent directory to sys.path to import codex_supervisor
sys.path.insert(0, str(Path(__file__).parent.parent))

from codex_supervisor.todo_generator import TodoGenerator


async def test_todo_generation():
    """Test TODO generation with sample config."""
    
    # Get API key from environment with fallback
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ Either OPENROUTER_API_KEY or OPENAI_API_KEY environment variable must be set")
        print("💡 Set it with: export OPENROUTER_API_KEY=your-key-here")
        print("💡 Or use: export OPENAI_API_KEY=your-key-here")
        return False
    
    # Load sample config
    config_file = Path(__file__).parent.parent / "configs" / "pentest_config.yaml"
    if not config_file.exists():
        print(f"❌ Config file not found: {config_file}")
        return False
    
    with open(config_file, 'r') as f:
        config_content = f.read()
    
    print("🎯 Testing TODO generation...")
    print(f"📁 Config file: {config_file}")
    print(f"📊 Config size: {len(config_content)} characters")
    
    try:
        # Generate TODOs
        use_openrouter = bool(os.getenv("OPENROUTER_API_KEY"))
        generator = TodoGenerator(api_key, use_openrouter)
        todos = await generator.generate_todos_from_config(config_content)
        
        # Count total todos
        def count_all_todos(todo_list):
            total = len(todo_list)
            for todo in todo_list:
                if todo.get("subtasks"):
                    total += count_all_todos(todo["subtasks"])
            return total
        
        total_count = count_all_todos(todos)
        
        print(f"✅ Generated {len(todos)} top-level TODOs ({total_count} total)")
        
        # Show summary
        print("\n📋 Generated TODOs:")
        for i, todo in enumerate(todos, 1):
            subtask_count = len(todo.get("subtasks", []))
            priority = todo.get("priority", "medium").upper()
            status = "📋" if todo.get("status") == "pending" else "✅"
            
            print(f"  {i}. {status} [{priority}] {todo.get('description', 'No description')}")
            if subtask_count > 0:
                print(f"     └── {subtask_count} subtasks")
        
        # Save to test output
        output_file = Path(__file__).parent / "generated_todos_test.json"
        await generator.save_todos_to_file(todos, output_file)
        print(f"\n💾 Saved test output to: {output_file}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error generating TODOs: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Main test function."""
    print("🚀 Testing TODO generation system...\n")
    
    success = await test_todo_generation()
    
    if success:
        print("\n🎉 TODO generation test passed!")
    else:
        print("\n💥 TODO generation test failed!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())