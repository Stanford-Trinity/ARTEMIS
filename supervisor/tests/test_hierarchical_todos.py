#!/usr/bin/env python3
"""
Test script for hierarchical TODO system in codex supervisor.
Tests all functionality including recursive operations and display formatting.
"""

import asyncio
import json
import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

# Add the parent directory to sys.path to import codex_supervisor
sys.path.insert(0, str(Path(__file__).parent.parent))

from codex_supervisor.tools import SupervisorTools


class MockInstanceManager:
    """Mock instance manager for testing."""
    pass

class MockLogReader:
    """Mock log reader for testing."""
    pass


class TestHierarchicalTodos:
    def __init__(self):
        """Initialize test environment with temporary directory."""
        self.temp_dir = None
        self.tools = None
        
    async def setup(self):
        """Set up test environment."""
        print("🔧 Setting up test environment...")
        
        # Create temporary directory
        self.temp_dir = Path(tempfile.mkdtemp())
        print(f"   📁 Temp directory: {self.temp_dir}")
        
        # Initialize tools with temp directory and mock objects
        mock_instance_manager = MockInstanceManager()
        mock_log_reader = MockLogReader()
        self.tools = SupervisorTools(
            instance_manager=mock_instance_manager,
            log_reader=mock_log_reader,
            session_dir=self.temp_dir
        )
        
        # Load sample data
        sample_file = Path(__file__).parent / "sample_todos.json"
        with open(sample_file, 'r') as f:
            sample_todos = json.load(f)
        
        # Save sample todos to temp directory
        await self.tools._save_todo_list(sample_todos)
        print("   ✅ Sample todos loaded")
        
    async def cleanup(self):
        """Clean up test environment."""
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
            print(f"🧹 Cleaned up temp directory: {self.temp_dir}")
    
    async def test_load_todos(self):
        """Test loading hierarchical todos."""
        print("\n📖 Testing todo loading...")
        
        todos = await self.tools._load_todo_list()
        assert len(todos) == 3, f"Expected 3 top-level todos, got {len(todos)}"
        
        # Check first todo has subtasks
        recon_todo = todos[0]
        assert recon_todo["id"] == "recon-001", f"Expected recon-001, got {recon_todo['id']}"
        assert len(recon_todo["subtasks"]) == 2, f"Expected 2 subtasks, got {len(recon_todo['subtasks'])}"
        
        # Check nested subtasks
        port_scan = recon_todo["subtasks"][0]
        assert len(port_scan["subtasks"]) == 2, f"Expected 2 nested subtasks, got {len(port_scan['subtasks'])}"
        
        print("   ✅ Todo loading works correctly")
    
    async def test_recursive_find(self):
        """Test recursive todo finding."""
        print("\n🔍 Testing recursive find...")
        
        todos = await self.tools._load_todo_list()
        
        # Test finding top-level todo
        found_todo, parent_list = self.tools._find_todo_recursive(todos, "recon-001")
        assert found_todo is not None, "Should find top-level todo"
        assert found_todo["id"] == "recon-001", f"Wrong todo found: {found_todo['id']}"
        assert parent_list is todos, "Parent list should be root todos"
        
        # Test finding nested todo
        found_todo, parent_list = self.tools._find_todo_recursive(todos, "recon-001-1-1")
        assert found_todo is not None, "Should find nested todo"
        assert found_todo["id"] == "recon-001-1-1", f"Wrong nested todo found: {found_todo['id']}"
        
        # Test finding non-existent todo
        found_todo, parent_list = self.tools._find_todo_recursive(todos, "nonexistent")
        assert found_todo is None, "Should not find non-existent todo"
        assert parent_list is None, "Parent list should be None for non-existent"
        
        print("   ✅ Recursive find works correctly")
    
    async def test_subtask_counting(self):
        """Test subtask counting."""
        print("\n🧮 Testing subtask counting...")
        
        todos = await self.tools._load_todo_list()
        
        # Test todo with subtasks
        recon_todo = todos[0]
        total, completed = self.tools._count_subtasks(recon_todo)
        assert total == 2, f"Expected 2 subtasks, got {total}"
        assert completed == 1, f"Expected 1 completed subtask, got {completed}"
        
        # Test todo without subtasks
        priv_esc_todo = todos[2]
        total, completed = self.tools._count_subtasks(priv_esc_todo)
        assert total == 0, f"Expected 0 subtasks, got {total}"
        assert completed == 0, f"Expected 0 completed subtasks, got {completed}"
        
        print("   ✅ Subtask counting works correctly")
    
    async def test_flatten_recursive(self):
        """Test recursive flattening."""
        print("\n🗂️ Testing recursive flattening...")
        
        todos = await self.tools._load_todo_list()
        flattened = self.tools._flatten_todos_recursive(todos)
        
        # Should have all todos at all levels
        assert len(flattened) == 11, f"Expected 11 total todos, got {len(flattened)}"
        
        # Check depth information
        depths = [depth for _, depth in flattened]
        assert 0 in depths, "Should have depth 0 (top-level)"
        assert 1 in depths, "Should have depth 1 (subtasks)"
        assert 2 in depths, "Should have depth 2 (nested subtasks)"
        assert max(depths) == 2, f"Max depth should be 2, got {max(depths)}"
        
        print("   ✅ Recursive flattening works correctly")
    
    async def test_default_read_view(self):
        """Test default read view (top-level with counts)."""
        print("\n👀 Testing default read view...")
        
        result = await self.tools._read_supervisor_todo({})
        
        # Should show top-level todos with subtask counts
        assert "📝 Supervisor Todo List:" in result, "Missing header"
        assert "📊 Progress:" in result, "Missing progress stats"
        assert "(2 subtasks, 1 completed)" in result, "Missing subtask count for recon"
        assert "(2 subtasks, 1 completed)" in result, "Missing subtask count for webapp"
        assert "(0 subtasks)" not in result, "Should not show 0 subtask count"
        assert "[recon-001]" in result, "Missing recon todo"
        assert "[webapp-001]" in result, "Missing webapp todo"
        assert "[priv-esc-001]" in result, "Missing priv-esc todo"
        
        # Should NOT show subtasks in default view
        assert "[recon-001-1]" not in result, "Should not show subtasks in default view"
        
        print("   ✅ Default read view works correctly")
    
    async def test_subtasks_drill_down(self):
        """Test drilling down into specific subtasks."""
        print("\n🔬 Testing subtasks drill-down...")
        
        # Test drilling into recon subtasks
        result = await self.tools._read_supervisor_todo({"item_id": "recon-001"})
        
        assert "📝 Subtasks of: Initial reconnaissance" in result, "Missing subtasks header"
        assert "[recon-001-1]" in result, "Missing first subtask"
        assert "[recon-001-2]" in result, "Missing second subtask"
        assert "├─ ✅" in result, "Missing completed subtask indicator"
        assert "├─ ⏳" in result, "Missing pending subtask indicator"
        
        # Test drilling into nested subtasks with depth
        result = await self.tools._read_supervisor_todo({"item_id": "recon-001-1", "depth": 2})
        
        assert "[recon-001-1-1]" in result, "Missing nested subtask"
        assert "[recon-001-1-2]" in result, "Missing nested subtask"
        
        print("   ✅ Subtasks drill-down works correctly")
    
    async def test_filtering(self):
        """Test filtering functionality."""
        print("\n🔍 Testing filtering...")
        
        # Test status filtering on top-level
        result = await self.tools._read_supervisor_todo({"filter_status": "pending"})
        assert "[recon-001]" in result, "Should show pending recon todo"
        assert "[webapp-001]" in result, "Should show pending webapp todo"
        assert "[priv-esc-001]" in result, "Should show pending priv-esc todo"
        
        # Test priority filtering
        result = await self.tools._read_supervisor_todo({"filter_priority": "high"})
        assert "[recon-001]" in result, "Should show high priority recon"
        assert "[webapp-001]" in result, "Should show high priority webapp"
        assert "[priv-esc-001]" not in result, "Should not show low priority priv-esc"
        
        # Test filtering on subtasks
        result = await self.tools._read_supervisor_todo({
            "item_id": "recon-001", 
            "filter_status": "completed"
        })
        assert "[recon-001-1]" in result, "Should show completed subtask"
        assert "[recon-001-2]" not in result, "Should not show pending subtask"
        
        print("   ✅ Filtering works correctly")
    
    async def test_add_subtask(self):
        """Test adding subtasks."""
        print("\n➕ Testing add subtask...")
        
        # Add subtask to existing todo
        result = await self.tools._update_supervisor_todo({
            "action": "add_subtask",
            "item_id": "priv-esc-001",
            "description": "Linux privilege escalation techniques",
            "priority": "medium",
            "notes": "Focus on kernel exploits"
        })
        
        assert "✅ Added subtask" in result, f"Subtask addition failed: {result}"
        assert "Linux privilege escalation" in result, "Missing subtask description"
        
        # Verify subtask was added
        todos = await self.tools._load_todo_list()
        priv_esc_todo = next(t for t in todos if t["id"] == "priv-esc-001")
        assert len(priv_esc_todo["subtasks"]) == 1, "Subtask was not added"
        
        new_subtask = priv_esc_todo["subtasks"][0]
        assert new_subtask["description"] == "Linux privilege escalation techniques", "Wrong subtask description"
        assert new_subtask["priority"] == "medium", "Wrong subtask priority"
        assert new_subtask["notes"] == "Focus on kernel exploits", "Wrong subtask notes"
        
        print("   ✅ Add subtask works correctly")
    
    async def test_nested_subtask_operations(self):
        """Test operations on nested subtasks."""
        print("\n🪆 Testing nested subtask operations...")
        
        # Add subtask to a subtask (3rd level nesting)
        result = await self.tools._update_supervisor_todo({
            "action": "add_subtask", 
            "item_id": "recon-001-2-1",
            "description": "Check for admin panels",
            "priority": "high"
        })
        
        assert "✅ Added subtask" in result, f"Nested subtask addition failed: {result}"
        
        # Update nested subtask
        todos = await self.tools._load_todo_list()
        nested_subtask_id = None
        for todo in todos:
            if todo["id"] == "recon-001":
                for subtask in todo["subtasks"]:
                    if subtask["id"] == "recon-001-2":
                        for nested in subtask["subtasks"]:
                            if nested["id"] == "recon-001-2-1":
                                if nested["subtasks"]:
                                    nested_subtask_id = nested["subtasks"][0]["id"]
                                break
        
        assert nested_subtask_id is not None, "Could not find nested subtask"
        
        # Complete the nested subtask
        result = await self.tools._update_supervisor_todo({
            "action": "complete",
            "item_id": nested_subtask_id
        })
        
        assert "✅ Completed todo item" in result, f"Nested completion failed: {result}"
        
        print("   ✅ Nested subtask operations work correctly")
    
    async def test_remove_subtask(self):
        """Test removing subtasks."""
        print("\n➖ Testing remove subtask...")
        
        # Remove a nested subtask
        result = await self.tools._update_supervisor_todo({
            "action": "remove",
            "item_id": "recon-001-1-2"
        })
        
        assert "✅ Removed todo item" in result, f"Subtask removal failed: {result}"
        
        # Verify subtask was removed
        todos = await self.tools._load_todo_list()
        port_scan_todo = None
        for todo in todos:
            if todo["id"] == "recon-001":
                for subtask in todo["subtasks"]:
                    if subtask["id"] == "recon-001-1":
                        port_scan_todo = subtask
                        break
        
        assert port_scan_todo is not None, "Could not find port scan todo"
        assert len(port_scan_todo["subtasks"]) == 1, f"Expected 1 subtask after removal, got {len(port_scan_todo['subtasks'])}"
        assert port_scan_todo["subtasks"][0]["id"] == "recon-001-1-1", "Wrong subtask remained"
        
        print("   ✅ Remove subtask works correctly")
    
    async def test_error_cases(self):
        """Test error handling."""
        print("\n❌ Testing error cases...")
        
        # Test adding subtask to non-existent parent
        result = await self.tools._update_supervisor_todo({
            "action": "add_subtask",
            "item_id": "nonexistent",
            "description": "This should fail"
        })
        assert "❌ Parent todo item with ID 'nonexistent' not found" in result
        
        # Test reading subtasks of non-existent item
        result = await self.tools._read_supervisor_todo({"item_id": "nonexistent"})
        assert "❌ Todo item with ID 'nonexistent' not found" in result
        
        # Test reading subtasks of item with no subtasks (initially)
        result = await self.tools._read_supervisor_todo({"item_id": "webapp-001-2"})
        assert "📝 Todo item 'Manual testing of key functions' has no subtasks." in result
        
        print("   ✅ Error handling works correctly")

    async def run_all_tests(self):
        """Run all tests."""
        print("🚀 Starting hierarchical TODO system tests...\n")
        
        try:
            await self.setup()
            
            # Run all test methods
            test_methods = [
                self.test_load_todos,
                self.test_recursive_find,
                self.test_subtask_counting,
                self.test_flatten_recursive,
                self.test_default_read_view,
                self.test_subtasks_drill_down,
                self.test_filtering,
                self.test_add_subtask,
                self.test_nested_subtask_operations,
                self.test_remove_subtask,
                self.test_error_cases,
            ]
            
            for test_method in test_methods:
                await test_method()
            
            print(f"\n🎉 All {len(test_methods)} tests passed successfully!")
            
        except Exception as e:
            print(f"\n💥 Test failed with error: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        finally:
            await self.cleanup()
        
        return True


async def main():
    """Main test runner."""
    tester = TestHierarchicalTodos()
    success = await tester.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())