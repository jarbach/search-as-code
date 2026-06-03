#!/usr/bin/env python3
"""
Cybersecurity Examples - Search-as-Code SDK

Demonstrates threat intelligence, CVE tracking, and security research workflows.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sdk import CybersecurityThreatIntelPipeline, SearchPipeline


async def example_1_threat_intel():
    """Basic threat intelligence gathering."""
    print("\n=== Example 1: Threat Intelligence ===\n")
    
    pipeline = CybersecurityThreatIntelPipeline(
        threat_type="ransomware attack healthcare",
        targets=["hospitals", "medical devices"],
        include_cve=True,
        time_range="month"
    )
    
    results = await pipeline.run()
    
    print(f"🛡️  Threat Intelligence Report")
    print(f"   Items found: {len(results.results)}")
    print(f"   Detailed records: {len(results.extracted_data)}")
    print(f"   Metrics: {results.metrics}")
    
    if results.extracted_data:
        print(f"\nSample extracted data:")
        for item in results.extracted_data[:2]:
            print(f"  - {item.get('title', 'N/A')[:80]}...")
            if item.get('severity'):
                print(f"    Severity: {item['severity']}")


async def example_2_cve_tracking():
    """Track CVEs for specific software."""
    print("\n=== Example 2: CVE Tracking ===\n")
    
    # Search for recent CVEs in specific software
    software_list = ["Chrome", "Firefox", "Safari"]
    
    for software in software_list:
        results = await SearchPipeline() \
            .search(f"CVE vulnerability {software} 2025 2026", time_range="month") \
            .filter(domain="|".join([
                "cve.mitre.org",
                "nvd.nist.gov",
                "github.com/advisories"
            ])) \
            .dedupe(by="url") \
            .compress(target_tokens=400) \
            .execute()
        
        print(f"{software}: {len(results.results)} recent CVEs")
        
        if results.results:
            top_result = results.results[0]
            print(f"  Latest: {top_result.title[:70]}...")
        
        await asyncio.sleep(1)  # Rate limiting


async def example_3_zero_day_monitoring():
    """Monitor for zero-day exploits."""
    print("\n=== Example 3: Zero-Day Monitoring ===\n")
    
    # Trusted security news sources
    security_sources = [
        "securityweek.com",
        "bleepingcomputer.com",
        "therecord.media",
        "mandiant.com",
        "crowdstrike.com",
        "krebsonsecurity.com"
    ]
    
    results = await SearchPipeline() \
        .fanout(
            variants=["zero-day", "0day", "in-the-wild exploit"],
            base_query="critical vulnerability",
            parallel=True
        ) \
        .filter(domain="|".join(security_sources)) \
        .dedupe(by="url") \
        .rank_by("relevance") \
        .execute()
    
    print(f"Zero-day monitoring results:")
    print(f"  Total items: {len(results.results)}")
    print(f"  Sources covered: {results.metrics.get('fanout_variants', 0)} query variants")
    
    # Show by source
    sources = {}
    for r in results.results:
        source = r.domain
        sources[source] = sources.get(source, 0) + 1
    
    print(f"\nBy source:")
    for source, count in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"  {source}: {count}")


async def example_4_threat_actor_tracking():
    """Track specific threat actors."""
    print("\n=== Example 4: Threat Actor Tracking ===\n")
    
    threat_actors = ["APT29", "Lazarus Group", "FIN7"]
    
    for actor in threat_actors:
        results = await SearchPipeline() \
            .search(f"{actor} campaign attribution") \
            .filter(domain="|".join([
                "mandiant.com",
                "crowdstrike.com",
                "microsoft.com/security",
                "symantec.com/threat-center"
            ])) \
            .compress(target_tokens=300) \
            .execute()
        
        print(f"{actor}: {len(results.results)} intelligence reports")
        
        if results.results:
            print(f"  Latest: {results.results[0].title[:60]}...")
        
        await asyncio.sleep(1)


async def example_5_vulnerability_research():
    """Deep dive into a specific vulnerability class."""
    print("\n=== Example 5: Vulnerability Class Research ===\n")
    
    vuln_class = "SQL injection"
    
    # Multi-aspect research
    aspects = [
        "detection techniques",
        "prevention best practices",
        "real-world examples 2025",
        "automated scanning tools",
        "remediation strategies"
    ]
    
    results = await SearchPipeline() \
        .fanout(aspects, base_query=vuln_class, parallel=True) \
        .dedupe(by="domain") \
        .fetch(max_concurrent=5) \
        .extract({
            "title": "h1",
            "content": ".article-body, .post-content",
            "date": ".date, time"
        }) \
        .compress(target_tokens=1000) \
        .execute()
    
    print(f"Vulnerability research: {vuln_class}")
    print(f"  Aspects covered: {len(aspects)}")
    print(f"  Pages fetched: {len(results.fetched_content)}")
    print(f"  Data extracted: {len(results.extracted_data)} records")
    
    if results.extracted_data:
        print(f"\nSample extraction:")
        sample = results.extracted_data[0]
        print(f"  Title: {sample.get('title', 'N/A')[:60]}...")
        if sample.get('date'):
            print(f"  Date: {sample['date']}")


async def example_6_security_vendor_comparison():
    """Compare security vendor threat reports."""
    print("\n=== Example 6: Vendor Report Comparison ===\n")
    
    vendors = [
        "CrowdStrike Global Threat Report",
        "Mandiant M-Trends",
        "Microsoft Digital Defense Report",
        "Verizon DBIR"
    ]
    
    results = await SearchPipeline() \
        .fanout(vendors, base_query="2025 2026", parallel=False) \
        .filter(exclude_domain="pdf") \
        .dedupe(by="url") \
        .rank_by("relevance") \
        .execute()
    
    print(f"Security vendor reports found:")
    
    # Group by vendor
    vendor_results = {v: [] for v in vendors}
    for r in results.results:
        for v in vendors:
            if v.split()[0].lower() in r.snippet.lower():
                vendor_results[v].append(r)
                break
    
    for vendor, items in vendor_results.items():
        if items:
            print(f"\n{vendor}:")
            print(f"  Found {len(items)} related resources")
            if items:
                print(f"  Top: {items[0].title[:60]}...")


async def main():
    print("Search-as-Code SDK - Cybersecurity Examples")
    print("=" * 60)
    print("⚠️  Note: These examples search for real security threats")
    print("=" * 60)
    
    examples = [
        ("Threat Intel", example_1_threat_intel),
        ("CVE Tracking", example_2_cve_tracking),
        ("Zero-Day Monitoring", example_3_zero_day_monitoring),
        ("Threat Actor Tracking", example_4_threat_actor_tracking),
        ("Vulnerability Research", example_5_vulnerability_research),
        ("Vendor Comparison", example_6_security_vendor_comparison),
    ]
    
    for name, func in examples:
        try:
            await func()
        except Exception as e:
            print(f"\n❌ {name} failed: {e}")
            import traceback
            traceback.print_exc()
        
        await asyncio.sleep(2)  # Rate limiting
    
    print("\n" + "=" * 60)
    print("All cybersecurity examples completed!")
    print("🔒 Stay safe out there!")


if __name__ == "__main__":
    asyncio.run(main())
