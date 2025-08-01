from django.db import migrations, connection

def check_table_exists(apps, schema_editor):
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
    # 使用原始SQL查询迁移数据
    with connection.cursor() as cursor:
        # 1. 迁移OpenAI数据
       
        # If rate_limit_rpm is not in the table, we assume it defaults to 0
        if cursor.description and 'rate_limit_rpm' not in [col[0] for col in cursor.description]:
            cursor.execute("""
                SELECT name, valid, api_key, base_url, model, translate_prompt,
                       content_translate_prompt, summary_prompt, temperature, top_p,
                       frequency_penalty, presence_penalty, max_tokens, is_ai, 0
                FROM translator_openaitranslator
            """)
        else:
            cursor.execute("""
            SELECT name, valid, api_key, base_url, model, translate_prompt,
                   content_translate_prompt, summary_prompt, temperature, top_p,
                   frequency_penalty, presence_penalty, max_tokens, is_ai, rate_limit_rpm
            FROM translator_openaitranslator
            """)
        for row in cursor.fetchall():
            cursor.execute("""
                INSERT INTO core_openaiagent (
                    name, valid, api_key, base_url, model, title_translate_prompt,
                    content_translate_prompt, summary_prompt, temperature, top_p,
                    frequency_penalty, presence_penalty, max_tokens, is_ai, rate_limit_rpm
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, row)
        
        # 2. 迁移DeepL数据
        cursor.execute("""
            SELECT name, valid, api_key, max_characters, server_url, proxy
            FROM translator_deepltranslator
        """)
        for row in cursor.fetchall():
            cursor.execute("""
                INSERT INTO core_deeplagent (
                    name, valid, api_key, max_characters, server_url, proxy
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, row)
        
        # 3. 迁移Test数据
        cursor.execute("""
            SELECT name, valid, translated_text, max_characters, interval, is_ai
            FROM translator_testtranslator
        """)
        for row in cursor.fetchall():
            cursor.execute("""
                INSERT INTO core_testagent (
                    name, valid, translated_text, max_characters, interval, is_ai
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, row)
    # 删除旧的translator表
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS translator_openaitranslator")
        cursor.execute("DROP TABLE IF EXISTS translator_deepltranslator")
        cursor.execute("DROP TABLE IF EXISTS translator_testtranslator")

class Migration(migrations.Migration):
    # 先检查translator_openaitranslator和translator_deepltranslator和translator_testtranslator表是否存在，如果不存在则不执行该迁移，如果存在则执行该迁移后删除这2个表
    dependencies = [
        ('core', '0024_deeplagent_openaiagent_testagent'),
    ]
    if not check_table_exists(None, None):
        operations = []
    else:
        print("Migration will proceed as at least one table exists")
        operations = [
            migrations.RunPython(migrate_translator_data),
        ]