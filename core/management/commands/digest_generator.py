from django.core.management.base import BaseCommand
from core.models.digest import Digest
from core.tasks.generate_digests import DigestGenerator
import logging
import sys

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Django management command to generate AI digest content.
    
    Usage:
        python manage.py generate_digests --publish-days monday    # Generate digests for Monday
        python manage.py generate_digests --publish-days tuesday   # Generate digests for Tuesday
    """
    
    help = 'Generate AI digest content for specified publish days'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--publish-days',
            type=str,
            required=True,
            help='Specify publish days to generate digests for (e.g., monday, tuesday)',
        )
    
    def handle(self, *args, **options):
        publish_days = options.get('publish_days')
        
        # Validate publish_days parameter
        valid_days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        if publish_days.lower() not in valid_days:
            self.stderr.write(
                self.style.ERROR(f'Error: Invalid publish_days. Valid options: {", ".join(valid_days)}')
            )
            sys.exit(1)
        
        # Get digests to process based on publish_days
        # Use JSON_EXTRACT for SQLite compatibility
        digests = Digest.objects.filter(
            is_active=True
        ).extra(
            where=["JSON_EXTRACT(publish_days, '$') LIKE ?"],
            params=[f'%{publish_days.lower()}%']
        )
        
        if not digests:
            self.stdout.write(
                self.style.WARNING(f'No active digests found for publish_days: {publish_days}')
            )
            return
        
        self.stdout.write(f'Found {len(digests)} digest(s) to process for {publish_days}')
        
        results = []
        for digest in digests:
            # # Check if generation is needed
            # if not digest.should_generate_today():
            #     self.stdout.write(
            #         self.style.WARNING(f'Skipping {digest.name} - already generated today or inactive')
            #     )
            #     continue
            
            # Generate digest
            self.stdout.write(f'Generating digest: {digest.name}')
            
            try:
                generator = DigestGenerator(digest)
                result = generator.generate(force=False)
                
                if result['success']:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'✓ Successfully generated "{digest.name}" '
                            f'({result.get("articles_processed", 0)} articles processed)'
                        )
                    )
                    results.append(('success', digest.name))
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f'✗ Failed to generate "{digest.name}": {result["error"]}'
                        )
                    )
                    results.append(('failed', digest.name, result['error']))
                    
            except Exception as e:
                error_msg = str(e)
                self.stdout.write(
                    self.style.ERROR(f'✗ Error generating "{digest.name}": {error_msg}')
                )
                results.append(('error', digest.name, error_msg))
                logger.error(f'Digest generation error for {digest.name}: {e}')
        
        # Summary
        successful = len([r for r in results if r[0] == 'success'])
        failed = len(results) - successful
        
        self.stdout.write('\n' + '='*50)
        self.stdout.write(f'Generation Summary:')
        self.stdout.write(f'  Successful: {successful}')
        self.stdout.write(f'  Failed: {failed}')
        
        if failed > 0:
            self.stdout.write('\nFailed digests:')
            for result in results:
                if result[0] != 'success':
                    self.stdout.write(f'  - {result[1]}: {result[2]}')