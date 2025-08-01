from django.db import migrations, connection
from utils.backup_db import backup_db
from django.contrib.contenttypes.models import ContentType

def check_table_exists():
     # 检查两个表是否存在
    cursor = connection.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='translator_openaitranslator'")
    oai_exists = cursor.fetchone() is not None
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='translator_deepltranslator'")
    deepl_exists = cursor.fetchone() is not None
    
    # 如果两个表都不存在，跳过迁移
    if not (oai_exists or deepl_exists):
        print("Skipping migration as both tables are missing")
        return False
    else:
        print("Proceeding with migration as at least one table exists")
        return True

def migrate_translator_data(apps, schema_editor):
    # 检查表是否存在
    if not check_table_exists():
        print("No tables to migrate, skipping migration.")
        return

    OpenAIAgent = apps.get_model('core', 'OpenAIAgent')
    DeepLAgent = apps.get_model('core', 'DeepLAgent')

    # 使用原始SQL查询迁移数据
    with connection.cursor() as cursor:
        # 1. 迁移OpenAI数据
        cursor.execute("""
                SELECT name, valid, api_key, base_url, model, translate_prompt,
                       content_translate_prompt, summary_prompt, temperature, top_p,
                       frequency_penalty, presence_penalty, max_tokens, is_ai
                FROM translator_openaitranslator
            """)

        for row in cursor.fetchall():
            OpenAIAgent.objects.create(
                name=row[0],
                valid=row[1],
                api_key=row[2],
                base_url=row[3],
                model=row[4],
                title_translate_prompt=row[5],
                content_translate_prompt=row[6],
                summary_prompt=row[7],
                temperature=row[8],
                top_p=row[9],
                frequency_penalty=row[10],
                presence_penalty=row[11],
                max_tokens=row[12],
                is_ai=row[13],
                rate_limit_rpm=0  # Default value
            )
        
        # 2. 迁移DeepL数据
        cursor.execute("""
            SELECT name, valid, api_key, max_characters, server_url, proxy
            FROM translator_deepltranslator
        """)
        for row in cursor.fetchall():
            DeepLAgent.objects.create(
                name=row[0],
                valid=row[1],
                api_key=row[2],
                max_characters=row[3],
                server_url=row[4],
                proxy=row[5]
            )
        

    # 删除旧的translator表
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS translator_openaitranslator")
        cursor.execute("DROP TABLE IF EXISTS translator_deepltranslator")
        cursor.execute("DROP TABLE IF EXISTS translator_testtranslator")

def update_feed_foreign_keys(apps, schema_editor): 
    # 获取新agent模型的ContentType
    Feed = apps.get_model('core', 'Feed')
    OpenAIAgent = apps.get_model('core', 'OpenAIAgent')
    DeepLAgent = apps.get_model('core', 'DeepLAgent')
    TestAgent = apps.get_model('core', 'TestAgent')
    
    # 创建ContentType映射字典
    content_type_map = {
        'openaitranslator': ContentType.objects.get_for_model(OpenAIAgent).id,
        'deepltranslator': ContentType.objects.get_for_model(DeepLAgent).id,
        'testtranslator': ContentType.objects.get_for_model(TestAgent).id,
    }
    
    # 更新所有Feed实例
    for feed in Feed.objects.all():
        # 更新翻译器
        if feed.translator_content_type:
            old_model = feed.translator_content_type.model
            if old_model in content_type_map:
                feed.translator_content_type_id = content_type_map[old_model]
            else:
                feed.translator_content_type_id = None
        
        # 更新摘要器
        if feed.summarizer_content_type:
            old_model = feed.summarizer_content_type.model
            if old_model in content_type_map:
                feed.summarizer_content_type_id = content_type_map[old_model]
            else:
                feed.summarizer_content_type_id = None
        
        # 保存更新
        feed.save()

class Migration(migrations.Migration):
    # 先检查translator_openaitranslator和translator_deepltranslator和translator_testtranslator表是否存在，如果不存在则不执行该迁移，如果存在则执行该迁移后删除这2个表
    dependencies = [
        ('core', '0024_deeplagent_openaiagent_testagent'),
        ('contenttypes', '__latest__'),  # 确保ContentType是最新的
    ]
    operations = [
            migrations.RunPython(backup_db),
            migrations.RunPython(migrate_translator_data),
            migrations.RunPython(update_feed_foreign_keys),
        ]