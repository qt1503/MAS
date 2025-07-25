import os

def DATA_DIR(dataset_name):
    # Lấy đường dẫn tuyệt đối của file config.py
    config_file_path = os.path.abspath(__file__)
    
    # Lấy thư mục mint (thư mục chứa config.py)
    mint_dir = os.path.dirname(config_file_path)
    
    # Lấy thư mục gốc project (cha của mint)
    project_root = os.path.dirname(mint_dir)
    
    # Tạo đường dẫn đến datasets
    return os.path.join(project_root, 'datasets', dataset_name)


