package com.day.cq.search
class PredicateGroup { Map map; static PredicateGroup create(Map m) { new PredicateGroup(map: m) } }
interface QueryBuilder { def createQuery(PredicateGroup g, Object session) }
