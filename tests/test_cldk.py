from unittest import TestCase

from cldk.analysis.python.treesitter import PythonSitter


class TestCLDK(TestCase):
    def setUp(self):
        """Runs before each test case"""
        self.cldk_python = PythonSitter()

    def tearDown(self):
        """Runs after each test case"""

    def test_get_methods(self):
        module_body = ''
        with open('../for_CLDK/django__django-12915/test_handlers_after.py', 'r') as f:
            module_body = f.read()
        methods = self.cldk_python.get_all_methods(module=module_body)
        method_names = [method.method_name for method in methods]
        self.assertIn('test_get_async_response', method_names)

    def test_get_class_methods(self):
        module_body = ''
        with open('../for_CLDK/django__django-12915/test_handlers_after.py', 'r') as f:
            module_body = f.read()
        classes = self.cldk_python.get_all_classes(module=module_body)
        start_lines = [[method.start_line for method in klazz.methods] for klazz in classes]
        end_lines = [[method.end_line for method in klazz.methods] for klazz in classes]
        class_and_method_names = [[klazz.class_name+'::'+method.method_name for method in klazz.methods] for klazz in classes]
        print(class_and_method_names)
        self.assertIn('TestASGIStaticFilesHandler::test_get_async_response', class_and_method_names[0])
