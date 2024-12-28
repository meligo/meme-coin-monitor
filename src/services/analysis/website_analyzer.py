import aiohttp
import mmh3
from bs4 import BeautifulSoup
from typing import Dict, List, Optional
from urllib.parse import urlparse
import re
import logging
from core.models.meme_coin import MemeCoin

logger = logging.getLogger(__name__)

class WebsiteAnalyzer:
    def __init__(self):
        self.typical_patterns = {
            'social_platforms': ['telegram', 'twitter', 'discord'],
            'token_metrics': ['supply', 'max supply', 'circulation', 'tax', 'liquidity'],
            'marketing_elements': ['roadmap', 'tokenomics', 'whitepaper']
        }

    async def analyze_website(self, url: str) -> Dict:
        """Analyze a meme coin website and generate fingerprints"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    html_content = await response.text()
                    return self._generate_fingerprints(html_content)
        except Exception as e:
            logger.error(f"Error analyzing website {url}: {str(e)}")
            return {}

    def _generate_fingerprints(self, html_content: str) -> Dict:
        """Generate comprehensive fingerprints for a meme coin website"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Extract key features
        features = {
            'token_elements': self._extract_token_elements(soup),
            'marketing_patterns': self._extract_marketing_patterns(soup),
            'social_presence': self._extract_social_links(soup),
            'launch_features': self._extract_launch_features(soup)
        }
        
        # Generate hashes
        structure_hash = self._generate_structure_hash(soup)
        content_hash = self._generate_content_hash(soup)
        feature_hash = self._generate_feature_hash(features)
        
        # Generate LSH bands for quick matching
        lsh_bands = self._generate_lsh_bands(structure_hash, content_hash, feature_hash)
        
        return {
            'structure_hash': structure_hash,
            'content_hash': content_hash,
            'feature_hash': feature_hash,
            'lsh_bands': lsh_bands,
            'features': features
        }

    def _extract_token_elements(self, soup: BeautifulSoup) -> Dict:
        """Extract token-specific elements"""
        elements = {
            'contract_display': None,
            'token_metrics': {},
            'buy_buttons': []
        }
        
        # Find contract address displays
        contract_pattern = re.compile(r'0x[a-fA-F0-9]{40}')
        for element in soup.find_all(string=contract_pattern):
            elements['contract_display'] = {
                'text': element.strip(),
                'context': element.parent.name,
                'location': self._get_element_path(element)
            }
        
        # Find token metrics
        metrics_keywords = ['supply', 'max supply', 'circulation', 'tax', 'liquidity']
        for keyword in metrics_keywords:
            elements['token_metrics'][keyword] = self._find_metric(soup, keyword)
        
        # Find buy/trade buttons
        buy_keywords = ['buy', 'trade', 'swap', 'get']
        for element in soup.find_all(['a', 'button']):
            text = element.get_text().lower()
            if any(keyword in text for keyword in buy_keywords):
                elements['buy_buttons'].append({
                    'text': text,
                    'type': element.name,
                    'location': self._get_element_path(element)
                })
        
        return elements

    def _extract_marketing_patterns(self, soup: BeautifulSoup) -> Dict:
        """Extract marketing-related patterns"""
        patterns = {}
        for section in ['tokenomics', 'roadmap', 'community', 'features']:
            patterns[section] = self._find_section(soup, section)
        return patterns

    def _extract_social_links(self, soup: BeautifulSoup) -> Dict:
        """Extract social media links"""
        social_links = {}
        for platform in self.typical_patterns['social_platforms']:
            links = soup.find_all('a', href=re.compile(platform))
            if links:
                social_links[platform] = [link['href'] for link in links]
        return social_links

    def _extract_launch_features(self, soup: BeautifulSoup) -> Dict:
        """Extract launch-related features"""
        features = {
            'presale': False,
            'launch_date': None,
            'whitelist': False
        }
        
        launch_keywords = ['presale', 'whitelist', 'launch']
        for keyword in launch_keywords:
            elements = soup.find_all(string=re.compile(keyword, re.IGNORECASE))
            if elements:
                features[keyword] = True
                
        return features

    def _generate_structure_hash(self, soup: BeautifulSoup) -> str:
        """Generate hash of website structure"""
        structure = []
        for tag in soup.find_all(True):
            structure.append(f"{tag.name}:{len(list(tag.children))}")
        return str(mmh3.hash(''.join(structure)))

    def _generate_content_hash(self, soup: BeautifulSoup) -> str:
        """Generate hash of website content"""
        content = soup.get_text().lower()
        return str(mmh3.hash(content))

    def _generate_feature_hash(self, features: Dict) -> str:
        """Generate hash of extracted features"""
        feature_str = str(sorted(str(features.items())))
        return str(mmh3.hash(feature_str))

    def _generate_lsh_bands(self, *hashes) -> List[str]:
        """Generate LSH bands for quick similarity matching"""
        combined = ''.join(hashes)
        bands = []
        for i in range(0, len(combined), 4):
            band = combined[i:i+4]
            if band:
                bands.append(str(mmh3.hash(band)))
        return bands

    def _get_element_path(self, element) -> str:
        """Get CSS-style path to an element"""
        path = []
        while element.parent:
            if element.parent.name:
                siblings = element.parent.find_all(element.name, recursive=False)
                if len(siblings) > 1:
                    index = siblings.index(element) + 1
                    path.append(f"{element.name}:nth-of-type({index})")
                else:
                    path.append(element.name)
            element = element.parent
        return ' > '.join(reversed(path))

    def _find_metric(self, soup: BeautifulSoup, keyword: str) -> Optional[str]:
        """Find and extract token metric values"""
        elements = soup.find_all(string=re.compile(keyword, re.IGNORECASE))
        if elements:
            # Try to find numeric value near the keyword
            for element in elements:
                text = element.parent.get_text()
                numbers = re.findall(r'\d+(?:\.\d+)?', text)
                if numbers:
                    return numbers[0]
        return None

    def _find_section(self, soup: BeautifulSoup, section_name: str) -> Dict:
        """Find and analyze a specific section"""
        section = {
            'present': False,
            'location': None,
            'content_length': 0
        }
        
        # Look for section heading or div
        elements = soup.find_all(string=re.compile(section_name, re.IGNORECASE))
        if elements:
            section['present'] = True
            element = elements[0]
            section['location'] = self._get_element_path(element)
            
            # Get section content
            parent = element.find_parent(['div', 'section'])
            if parent:
                section['content_length'] = len(parent.get_text())
                
        return section

website_analyzer = WebsiteAnalyzer()